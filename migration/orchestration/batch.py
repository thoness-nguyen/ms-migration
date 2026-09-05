from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ..common.checkpoint import StateStore
from ..common.graph import GraphError
from ..common.retry import with_retry
from ..onedrive.migration import copy_drive, retry_failed_onedrive_files
from ..sharepoint.migration import copy_library, retry_failed_sharepoint_files
from ..sharepoint.services import get_site_libraries, resolve_site_url
from ..sharepoint.validation import validate_library_migration
from ..teams.services import extract_chat, import_chat


def load_plan(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        plan = yaml.safe_load(stream) if path.suffix.lower() in {".yaml", ".yml"} else json.load(stream)
    if not isinstance(plan, dict):
        raise TypeError("Batch plan must be an object")
    if "users" in plan:
        return load_hierarchical_plan(plan, path.parent)
    if not isinstance(plan.get("chats"), list):
        raise TypeError("Batch plan must contain a chats list")
    return plan


def _load_data(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream) if path.suffix.lower() in {".yaml", ".yml"} else json.load(stream)


def load_hierarchical_plan(plan: dict[str, Any], plan_dir: Path) -> dict[str, Any]:
    users = _load_data((plan_dir / plan["users"]).resolve()) if isinstance(plan["users"], str) else plan["users"]
    if not isinstance(users, list):
        raise TypeError("users must be a list or a mapping-file path")
    user_map: dict[str, str] = {}
    for user in users:
        if not isinstance(user, dict) or not user.get("key") or not user.get("source_id") or not user.get("target_id"):
            raise ValueError("Each user requires key, source_id, and target_id")
        if user["key"] in user_map:
            raise ValueError(f"Duplicate user key: {user['key']}")
        user_map[user["key"]] = user["target_id"]

    workloads = plan.get("workloads", {})
    if not isinstance(workloads, dict):
        raise TypeError("workloads must be an object")
    teams = _load_data((plan_dir / workloads["teams"]).resolve()) if isinstance(workloads.get("teams"), str) else workloads.get("teams", {})
    chats = []
    for entry in teams.get("chats", []) if isinstance(teams, dict) else []:
        member_keys = entry.get("users", [])
        missing = [key for key in member_keys if key not in user_map]
        if missing:
            raise ValueError(f"Chat {entry.get('name', entry.get('source_chat_id'))} references unknown users: {', '.join(missing)}")
        chats.append({"name": entry.get("name"), "source_chat_id": entry.get("source_chat_id"), "user_map": {next(user["source_id"] for user in users if user["key"] == key): user_map[key] for key in member_keys}})
    normalized_workloads: dict[str, Any] = {key: value for key, value in workloads.items() if key != "teams"}
    if isinstance(normalized_workloads.get("onedrive"), str):
        normalized_workloads["onedrive"] = _load_data((plan_dir / normalized_workloads["onedrive"]).resolve())
    if isinstance(normalized_workloads.get("exchange"), str):
        normalized_workloads["exchange"] = _load_data((plan_dir / normalized_workloads["exchange"]).resolve())
    if isinstance(normalized_workloads.get("sharepoint"), str):
        normalized_workloads["sharepoint"] = _load_data((plan_dir / normalized_workloads["sharepoint"]).resolve())
    for workload in ("onedrive", "exchange"):
        entries = normalized_workloads.get(workload, {})
        for entry in entries.get("users", entries.get("mailboxes", [])) if isinstance(entries, dict) else []:
            if entry.get("user") not in user_map:
                raise ValueError(f"{workload} mapping references unknown user: {entry.get('user')}")
    normalized = {"migration_id": plan.get("migration_id"), "chats": chats, "workloads": normalized_workloads}
    normalized["users"] = users
    return normalized


def _mapping(entry: dict[str, Any], plan_dir: Path) -> dict[str, str]:
    mapping = entry.get("user_map")
    if isinstance(mapping, str):
        mapping_path = (plan_dir / mapping).resolve()
        with mapping_path.open(encoding="utf-8") as stream:
            mapping = json.load(stream)
    if not isinstance(mapping, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in mapping.items()):
        raise ValueError("Each chat user_map must be an object or a JSON file path")
    return mapping


def _concurrency(plan: dict[str, Any], workload: str, stage: str, default: int) -> int:
    """Optional performance.concurrency.<workload>.<stage> config (ARCHITECHTURE_UPGRADE.md ss9/ss25).
    Absent entirely by default, so existing plans behave the same except for
    the new (safe default) concurrency instead of one-item-at-a-time."""
    concurrency = plan.get("performance", {}).get("concurrency", {}) if isinstance(plan.get("performance"), dict) else {}
    workload_config = concurrency.get(workload, {}) if isinstance(concurrency, dict) else {}
    value = workload_config.get(stage, default) if isinstance(workload_config, dict) else default
    return int(value) if isinstance(value, (int, float)) else default


def _run_batch_chats(source_graph: Any, target_graph: Any, plan: dict[str, Any], plan_dir: Path, state: StateStore, dry_run: bool) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, entry in enumerate(plan.get("chats", []), start=1):
        name = entry.get("name", f"chat-{index}")
        result: dict[str, Any] = {"name": name, "source_chat_id": entry.get("source_chat_id"), "status": "failed", "messages": 0}
        try:
            source_chat_id = entry.get("source_chat_id")
            if not isinstance(source_chat_id, str) or not source_chat_id:
                raise ValueError("source_chat_id is required")
            if state.status("batch-chat", source_chat_id) == "completed":
                result["status"] = "skipped"
                result["reason"] = "already completed"
                results.append(result)
                continue
            user_map = _mapping(entry, plan_dir)
            bundle = extract_chat(source_graph, source_chat_id)
            member_ids = {member.get("userId") for member in bundle["members"]}
            missing = sorted(member_id for member_id in member_ids if member_id not in user_map)
            if missing:
                raise ValueError(f"Missing target mappings for source users: {', '.join(str(item) for item in missing)}")
            bundle_path = plan_dir / f"{name}.chat-bundle.json"
            bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
            stats: dict[str, int] = {}
            target_chat_id, count = import_chat(target_graph, bundle, user_map, state, dry_run, stats)
            result.update({"status": "dry-run" if dry_run else "completed", "target_chat_id": target_chat_id, "messages": count, "bundle": str(bundle_path), "skipped_system_messages": stats.get("skipped_system", 0), "unsupported_features": stats.get("unsupported_features", 0)})
            if not dry_run:
                state.mark("batch-chat", source_chat_id, "completed", target_chat_id)
        except (GraphError, OSError, TypeError, ValueError, KeyError) as error:
            result["error"] = str(error)
            if not dry_run:
                state.mark("batch-chat", str(entry.get("source_chat_id", name)), "failed", detail=str(error))
        results.append(result)
    return results


def _run_batch_onedrive(source_graph: Any, target_graph: Any, plan: dict[str, Any], state: StateStore, dry_run: bool, retry_failed_only: bool = False) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    content_concurrency = _concurrency(plan, "onedrive", "content", default=4)
    for entry in plan.get("workloads", {}).get("onedrive", {}).get("users", []):
        user_key = entry.get("user")
        user = next((item for item in plan.get("users", []) if item.get("key") == user_key), None)
        result: dict[str, Any] = {"workload": "onedrive", "user": user_key, "status": "failed", "items": 0}
        try:
            if not user:
                raise ValueError(f"Unknown OneDrive user: {user_key}")
            source_id = user["source_id"]
            target_id = user["target_id"]
            if state.status("batch-onedrive", source_id) == "completed":
                result.update({"status": "skipped", "reason": "already completed"})
            elif retry_failed_only:
                print(f"[DEBUG] Retry-failed-only mode: re-attempting previously failed items for OneDrive user {user_key}")
                stats = retry_failed_onedrive_files(source_graph, target_graph, source_id, target_id, state, content_concurrency=content_concurrency)
                failed_count = stats.get("failed", 0) + stats.get("still_missing_parent", 0)
                result.update({"status": "completed" if failed_count == 0 else "completed_with_failures", "items": stats.get("files_copied", 0), "stats": stats})
                if failed_count == 0:
                    state.mark("batch-onedrive", source_id, "completed", target_id)
                else:
                    state.mark("batch-onedrive", source_id, "pending", detail=f"{failed_count} item(s) still failing after targeted retry")
            else:
                stats, success, last_error, attempts = with_retry(
                    copy_drive, source_graph, target_graph, source_id, target_id, state, dry_run,
                    content_concurrency=content_concurrency,
                    max_retries=3,
                    on_retry=lambda attempt, error: print(f"[RETRY] OneDrive {user_key} attempt {attempt} failed: {type(error).__name__}: {error}"),
                )
                if success:
                    failed_count = stats.get("failed", 0) if isinstance(stats, dict) else 0
                    items = stats.get("files_copied", 0) if isinstance(stats, dict) else stats
                    fully_complete = failed_count == 0
                    result.update({"status": ("dry-run" if dry_run else ("completed" if fully_complete else "completed_with_failures")), "items": items, "stats": stats if isinstance(stats, dict) else None, "retry_count": attempts - 1})
                    if not dry_run:
                        if fully_complete:
                            state.mark("batch-onedrive", source_id, "completed", target_id)
                        else:
                            # Per-item failures exist - do NOT mark complete, or a future
                            # run would skip this user and never retry the missing items.
                            state.mark("batch-onedrive", source_id, "pending", detail=f"{failed_count} item(s) failed - will retry on next run")
                else:
                    error_msg = f"{type(last_error).__name__}: {str(last_error)[:200]}" if last_error else "Copy failed"
                    result.update({"status": "failed", "error": error_msg, "retry_count": attempts - 1})
                    if not dry_run:
                        state.mark("batch-onedrive", str(user_key), "failed", detail=error_msg[:200])
        except (GraphError, OSError, TypeError, ValueError, KeyError) as error:
            result["error"] = str(error)
            if not dry_run:
                state.mark("batch-onedrive", str(user_key), "failed", detail=str(error))
        results.append(result)
    return results


def _run_batch_sharepoint(source_graph: Any, target_graph: Any, plan: dict[str, Any], state: StateStore, dry_run: bool, retry_failed_only: bool = False) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    content_concurrency = _concurrency(plan, "sharepoint", "content", default=4)
    for site in plan.get("workloads", {}).get("sharepoint", {}).get("sites", []):
        # Resolve site IDs from URLs if provided
        source_site_id = site.get("source_site_id")
        target_site_id = site.get("target_site_id")
        source_site_url = site.get("source_site_url")
        target_site_url = site.get("target_site_url")
        
        if source_site_url and not source_site_id:
            try:
                print(f"[DEBUG] Resolving source URL: {source_site_url}")
                source_site_id = resolve_site_url(source_graph, source_site_url)
                print(f"[DEBUG] Resolved source site ID: {source_site_id}")
            except GraphError as e:
                results.append({
                    "workload": "sharepoint",
                    "site": site.get("name", source_site_url),
                    "status": "failed",
                    "error": f"Cannot resolve source site URL: {str(e)[:100]}"
                })
                continue
        
        if target_site_url and not target_site_id:
            try:
                print(f"[DEBUG] Resolving target URL: {target_site_url}")
                target_site_id = resolve_site_url(target_graph, target_site_url)
                print(f"[DEBUG] Resolved target site ID: {target_site_id}")
            except GraphError as e:
                results.append({
                    "workload": "sharepoint",
                    "site": site.get("name", target_site_url),
                    "status": "failed",
                    "error": f"Cannot resolve target site URL: {str(e)[:100]}"
                })
                continue
        
        site_name = site.get("name", f"site-{source_site_url or source_site_id}")
        
        if not source_site_id or not target_site_id:
            results.append({"workload": "sharepoint", "site": site_name, "status": "skipped", "reason": "source_site_id/url or target_site_id/url missing"})
            continue
        
        print(f"\n[DEBUG] Processing site: {site_name}")
        print(f"[DEBUG] Source site ID: {source_site_id}")
        print(f"[DEBUG] Target site ID: {target_site_id}")
        
        result: dict[str, Any] = {"workload": "sharepoint", "site": site_name, "source_site_id": source_site_id, "status": "failed", "libraries": []}
        
        try:
            try:
                print(f"[DEBUG] Discovering libraries in source site...")
                source_libs = get_site_libraries(source_graph, source_site_id)
                print(f"[DEBUG] Found {len(source_libs)} libraries in source site")
            except GraphError as e:
                raise ValueError(f"Cannot access source site {source_site_id}: {str(e)[:100]}")
            
            if not source_libs:
                result.update({"status": "skipped", "reason": "no libraries found"})
                results.append(result)
                continue
            
            try:
                print(f"[DEBUG] Discovering libraries in target site...")
                target_libs = get_site_libraries(target_graph, target_site_id)
                print(f"[DEBUG] Found {len(target_libs)} libraries in target site")
            except GraphError as e:
                raise ValueError(f"Cannot access target site {target_site_id}: {str(e)[:100]}")
            
            target_libs_by_name = {lib["name"]: lib["id"] for lib in target_libs}
            
            for src_lib in source_libs:
                src_lib_id = src_lib["id"]
                src_lib_name = src_lib.get("name", "Documents")
                tgt_lib_id = target_libs_by_name.get(src_lib_name)
                
                lib_result: dict[str, Any] = {"name": src_lib_name, "status": "failed"}
                
                if not tgt_lib_id:
                    lib_result.update({"status": "skipped", "reason": "target library not found"})
                    result["libraries"].append(lib_result)
                    continue
                
                state_key = f"{source_site_id}:{src_lib_id}"
                
                if state.status("batch-sharepoint", state_key) == "completed":
                    lib_result.update({"status": "skipped", "reason": "already completed"})
                    result["libraries"].append(lib_result)
                    continue
                
                try:
                    print(f"[DEBUG] Copying library: {src_lib_name}")
                    if dry_run:
                        # In dry-run, skip file traversal to avoid timeout - just report success
                        print(f"[DEBUG] Dry-run mode: skipping file traversal for {src_lib_name}")
                        lib_result.update({"status": "dry-run", "items": "N/A (skipped traversal)", "note": "Run without --dry-run to copy"})
                    elif retry_failed_only:
                        # Skip the (potentially hours-long) full tree walk on a mostly-done
                        # library - just re-attempt the small set already marked "failed".
                        print(f"[DEBUG] Retry-failed-only mode: re-attempting previously failed items in {src_lib_name}")
                        stats = retry_failed_sharepoint_files(
                            source_graph, target_graph, src_lib_id, tgt_lib_id, state,
                            content_concurrency=content_concurrency,
                        )
                        total_items = stats.get("files_copied", 0)
                        failed_count = stats.get("failed", 0) + stats.get("still_missing_parent", 0)
                        validation = validate_library_migration(source_graph, target_graph, src_lib_id, tgt_lib_id)
                        validated = validation.get("match", False) or validation.get("status") == "error"
                        fully_complete = failed_count == 0 and validated
                        lib_result.update({
                            "status": "completed" if fully_complete else "completed_with_failures",
                            "items": total_items,
                            "stats": stats,
                            "validation": validation,
                        })
                        if fully_complete:
                            state.mark("batch-sharepoint", state_key, "completed", tgt_lib_id)
                        else:
                            reason = f"{failed_count} item(s) still failing" if failed_count else "source/target count mismatch"
                            state.mark("batch-sharepoint", state_key, "pending",
                                       detail=f"{reason} after targeted retry - consider a full discovery run")
                    else:
                        stats, success, last_error, attempts = with_retry(
                            copy_library, source_graph, target_graph, src_lib_id, tgt_lib_id, state, dry_run,
                            content_concurrency=content_concurrency,
                            max_retries=3, backoff_base_seconds=10,
                            on_retry=lambda attempt, error: print(f"[RETRY] {src_lib_name} attempt {attempt} failed: {type(error).__name__}: {error}"),
                        )
                        
                        if success:
                            total_items = stats.get("files_copied", 0) + stats.get("folders_created", 0)
                            failed_count = stats.get("failed", 0)
                            # ADR "Validate" step: confirm source/target counts line up after the copy
                            validation = validate_library_migration(source_graph, target_graph, src_lib_id, tgt_lib_id)
                            # A clean stats["failed"] count doesn't guarantee nothing was lost
                            # (e.g. a whole subtree skipped by a transient listing error) - trust
                            # the independent count comparison too before declaring victory.
                            validated = validation.get("match", False) or validation.get("status") == "error"
                            fully_complete = failed_count == 0 and validated
                            lib_result.update({
                                "status": "completed" if fully_complete else "completed_with_failures",
                                "items": total_items,
                                "stats": stats,
                                "retries": attempts - 1,
                                "validation": validation,
                            })
                            if not dry_run:
                                if fully_complete:
                                    state.mark("batch-sharepoint", state_key, "completed", tgt_lib_id)
                                else:
                                    # Per-item failures and/or a count mismatch exist - do NOT mark
                                    # the library complete, or future runs would skip it and never
                                    # retry the missing items.
                                    reason = f"{failed_count} item(s) failed" if failed_count else "source/target count mismatch"
                                    state.mark("batch-sharepoint", state_key, "pending",
                                               detail=f"{reason} - will retry on next run")
                        else:
                            error_msg = f"{type(last_error).__name__}: {str(last_error)[:200]}" if last_error else "Copy failed"
                            lib_result.update({
                                "status": "failed",
                                "error": error_msg,
                                "items": 0,
                                "retries": attempts - 1,
                            })
                            if not dry_run:
                                state.mark("batch-sharepoint", state_key, "failed",
                                         detail=f"Retried {attempts - 1} times, failed: {error_msg[:150]}")
                
                except (GraphError, OSError, TypeError, ValueError, KeyError) as error:
                    lib_result["error"] = str(error)[:100]
                    if not dry_run:
                        state.mark("batch-sharepoint", state_key, "failed", detail=str(error)[:200])
                
                result["libraries"].append(lib_result)
            
            result.update({"status": "dry-run" if dry_run else "completed"})
            
        except (GraphError, OSError, TypeError, ValueError, KeyError) as error:
            result["error"] = str(error)
            if not dry_run:
                state.mark("batch-sharepoint", f"{source_site_id}:all", "failed", detail=str(error)[:200])
        
        results.append(result)
    return results


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll the per-site/per-user results up into one unambiguous answer to
    "is the migration actually done?" - a mismatch or a handful of failed
    items still leaves the overall run "completed" (every site/user was
    processed), it just means some individual items need a retry pass."""
    failed_items = 0
    mismatched_libraries: list[str] = []
    incomplete_areas: list[str] = []
    for result in results:
        workload = result.get("workload")
        if workload == "sharepoint":
            for lib in result.get("libraries", []):
                stats = lib.get("stats") or {}
                failed_items += stats.get("failed", 0)
                validation = lib.get("validation") or {}
                if validation and not validation.get("match", True) and validation.get("status") != "error":
                    mismatched_libraries.append(f"{result.get('site')}/{lib.get('name')}")
                if lib.get("status") == "failed":
                    incomplete_areas.append(f"sharepoint:{result.get('site')}/{lib.get('name')}")
        elif workload == "onedrive":
            stats = result.get("stats") or {}
            failed_items += stats.get("failed", 0)
            if result.get("status") == "failed":
                incomplete_areas.append(f"onedrive:{result.get('user')}")
        elif ("source_chat_id" in result or result.get("name")) and result.get("status") == "failed":
            incomplete_areas.append(f"teams-chat:{result.get('name')}")

    if incomplete_areas:
        overall = "incomplete"
    elif failed_items or mismatched_libraries:
        overall = "completed_with_failed_items"
    else:
        overall = "completed"

    return {
        "overall_status": overall,
        "failed_item_count": failed_items,
        "mismatched_libraries": mismatched_libraries,
        "areas_needing_attention": incomplete_areas,
    }


def _print_summary(summary: dict[str, Any]) -> None:
    print("\n" + "=" * 80)
    if summary["overall_status"] == "completed":
        print("✅ Migration run COMPLETE - every item copied and source/target counts match.")
    elif summary["overall_status"] == "completed_with_failed_items":
        print("✅ Migration run COMPLETE (every site/user was processed), but ⚠ not every")
        print(f"   item copied successfully: {summary['failed_item_count']} item(s) are marked 'failed'")
        if summary["mismatched_libraries"]:
            print(f"   and these libraries have a source/target count mismatch: {', '.join(summary['mismatched_libraries'])}")
        print("   These are tracked in the checkpoint DB and safe to retry without redoing")
        print("   the whole run - use panel option 6 (Retry failed items) or")
        print("   'tenant-migrator batch ... --retry-failed-only'.")
    else:
        print("❌ Migration run INCOMPLETE - the following did not finish at all:")
        for area in summary["areas_needing_attention"]:
            print(f"   - {area}")
        print("   Re-run 'Start migration' (option 2) to retry these from where they left off.")
    print("=" * 80 + "\n")


def run_batch(source_graph: Any, target_graph: Any, plan: dict[str, Any], plan_dir: Path, state: StateStore, output: Path, dry_run: bool = False, retry_failed_only: bool = False) -> dict[str, Any]:
    """Coordinate execution across domains. Contains no domain-specific migration logic itself."""
    results: list[dict[str, Any]] = []
    results.extend(_run_batch_chats(source_graph, target_graph, plan, plan_dir, state, dry_run))
    results.extend(_run_batch_onedrive(source_graph, target_graph, plan, state, dry_run, retry_failed_only))
    results.extend(_run_batch_sharepoint(source_graph, target_graph, plan, state, dry_run, retry_failed_only))
    planned = {workload: value for workload, value in plan.get("workloads", {}).items() if workload != "teams"}
    summary = _summarize(results)
    report = {"migration_id": plan.get("migration_id"), "generated_at": datetime.now(UTC).isoformat(), "dry_run": dry_run, "summary": summary, "results": results, "planned_workloads": planned}
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not dry_run:
        _print_summary(summary)
    return report

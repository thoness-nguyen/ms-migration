from __future__ import annotations

import argparse
import csv
import hashlib
import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

from migration.common.auth import target_token
from migration.common.graph import GraphClient


# ============================================================
# Environment
# ============================================================

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
load_dotenv(ENV_FILE)


# ============================================================
# Configuration
# ============================================================

REPORT_DIR = REPO_ROOT / "reports"

DEFAULT_REPORT_NAME = "account-password-reset-report.csv"
DEFAULT_META_NAME = "account-password-reset-report.meta.txt"
DEFAULT_RESULT_NAME = "account-password-reset-result.csv"

DEFAULT_RECENT_LOGIN_DAYS = 30
DEFAULT_PASSWORD_LENGTH = 20

# Users with these directory roles are skipped automatically.
# This is intentionally conservative.
PROTECTED_ROLE_NAMES = {
    "Global Administrator",
    "Privileged Role Administrator",
    "User Administrator",
    "Authentication Administrator",
    "Privileged Authentication Administrator",
}


# ============================================================
# Environment validation
# ============================================================

def validate_environment() -> None:
    """Validate the environment variables required by Graph auth."""

    required_variables = [
        "TARGET_TENANT_ID",
        "TARGET_GRAPH_CLIENT_ID",
        "TARGET_GRAPH_CLIENT_SECRET",
    ]

    missing = [
        name for name in required_variables
        if not os.getenv(name)
    ]

    if missing:
        print()
        print("=" * 70)
        print("MISSING ENVIRONMENT CONFIGURATION")
        print("=" * 70)
        print()
        print(f".env expected at: {ENV_FILE}")
        print()
        print("Missing variables:")
        for name in missing:
            print(f"  - {name}")

        print()
        print("Expected .env entries:")
        print()
        print("TARGET_TENANT_ID=<tenant-id>")
        print("TARGET_GRAPH_CLIENT_ID=<client-id>")
        print("TARGET_GRAPH_CLIENT_SECRET=<client-secret>")
        print()

        raise SystemExit(
            "Environment configuration is incomplete."
        )


# ============================================================
# File paths
# ============================================================

def get_report_path(report_name: str | None = None) -> Path:
    """Return the report path."""

    return REPORT_DIR / (
        report_name or DEFAULT_REPORT_NAME
    )


def get_meta_path(report_path: Path) -> Path:
    """Return the metadata path corresponding to a report."""

    return report_path.with_suffix(".meta.txt")


def get_result_path(report_path: Path) -> Path:
    """Return the execution result path."""

    return report_path.with_name(DEFAULT_RESULT_NAME)


# ============================================================
# Password generation
# ============================================================

def generate_password(
    length: int = DEFAULT_PASSWORD_LENGTH,
) -> str:
    """
    Generate a cryptographically secure temporary password.

    Ensures at least:
      - 1 lowercase
      - 1 uppercase
      - 1 digit
      - 1 special character
    """

    if length < 16:
        raise ValueError(
            "Password length must be at least 16 characters."
        )

    lowercase = string.ascii_lowercase
    uppercase = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%^&*-_+="

    alphabet = (
        lowercase
        + uppercase
        + digits
        + special
    )

    password = [
        secrets.choice(lowercase),
        secrets.choice(uppercase),
        secrets.choice(digits),
        secrets.choice(special),
    ]

    password.extend(
        secrets.choice(alphabet)
        for _ in range(length - len(password))
    )

    secrets.SystemRandom().shuffle(password)

    return "".join(password)


# ============================================================
# Date handling
# ============================================================

def parse_datetime(
    value: str | None,
) -> datetime | None:
    """Parse Microsoft Graph ISO 8601 datetime."""

    if not value:
        return None

    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


# ============================================================
# User classification
# ============================================================

def has_license(
    user: dict[str, Any],
) -> bool:
    """Return True when the user has at least one assigned license."""

    return bool(user.get("assignedLicenses"))


def get_last_successful_sign_in(
    user: dict[str, Any],
) -> datetime | None:
    """
    Return the user's last successful sign-in.

    Uses lastSuccessfulSignInDateTime rather than
    lastSignInDateTime because the latter can represent
    unsuccessful or interrupted sign-in activity.
    """

    sign_in_activity = user.get("signInActivity") or {}

    return parse_datetime(
        sign_in_activity.get(
            "lastSuccessfulSignInDateTime"
        )
    )


def classify_user(
    user: dict[str, Any],
    recent_login_days: int = DEFAULT_RECENT_LOGIN_DAYS,
    activity_check_enabled: bool = True,
) -> tuple[str, str]:
    """
    Determine whether the user should be reset.

    Returns:
        ("RESET", reason)
        ("SKIP", reason)

    Important:
        When activity_check_enabled is False, this function does
        NOT claim that the user is inactive. It only applies the
        non-activity eligibility rules.
    """

    # --------------------------------------------------------
    # License
    # --------------------------------------------------------

    if not has_license(user):
        return (
            "SKIP",
            "No license assigned",
        )

    # --------------------------------------------------------
    # Account enabled
    # --------------------------------------------------------

    if not user.get("accountEnabled", True):
        return (
            "SKIP",
            "Account disabled",
        )

    # --------------------------------------------------------
    # User type
    # --------------------------------------------------------

    user_type = user.get("userType")

    if user_type != "Member":
        return (
            "SKIP",
            f"User type is {user_type or 'unknown'}",
        )

    # --------------------------------------------------------
    # Recent successful login
    # --------------------------------------------------------

    if activity_check_enabled:
        last_successful = get_last_successful_sign_in(user)

        if last_successful:
            cutoff = (
                datetime.now(timezone.utc)
                - timedelta(days=recent_login_days)
            )

            if last_successful >= cutoff:
                return (
                    "SKIP",
                    (
                        "Successful sign-in within "
                        f"{recent_login_days} days"
                    ),
                )

        # No sign-in activity is treated as eligible when the
        # activity check is available. This preserves the original
        # behavior of the script.
        return (
            "RESET",
            "Licensed and no recent successful sign-in",
        )

    # --------------------------------------------------------
    # Non-Premium tenant mode
    # --------------------------------------------------------

    return (
        "RESET",
        (
            "Licensed, enabled Member; activity check disabled "
            "because signInActivity requires Entra ID Premium"
        ),
    )


# ============================================================
# Graph client
# ============================================================

def build_graph_client() -> GraphClient:
    """Build Graph client using the existing target-tenant auth."""

    validate_environment()

    return GraphClient(
        target_token()
    )


# ============================================================
# User retrieval
# ============================================================

def get_users(
    client: GraphClient,
    activity_check_enabled: bool = True,
) -> Iterable[dict[str, Any]]:
    """
    Retrieve all users using Graph pagination.

    signInActivity is requested ONLY when activity_check_enabled
    is True. This is important because requesting signInActivity
    can fail on tenants without Entra ID Premium P1/P2.
    """

    select_fields = [
        "id",
        "displayName",
        "userPrincipalName",
        "mail",
        "accountEnabled",
        "userType",
        "assignedLicenses",
    ]

    if activity_check_enabled:
        select_fields.append("signInActivity")

    path = (
        "/users"
        f"?$select={','.join(select_fields)}"
        "&$top=100"
    )

    return client.pages(path)


# ============================================================
# Role protection
# ============================================================

def get_role_definitions(
    client: GraphClient,
) -> dict[str, str]:
    """
    Retrieve Microsoft Entra directory role definitions.

    Returns:
        {
            role_definition_id: role_display_name
        }
    """

    role_definitions: dict[str, str] = {}

    path = (
        "/roleManagement/directory/"
        "roleDefinitions"
    )

    for definition in client.pages(path):
        definition_id = definition.get("id")
        display_name = definition.get("displayName")

        if definition_id and display_name:
            role_definitions[definition_id] = display_name

    return role_definitions


def get_protected_user_ids(
    client: GraphClient,
) -> set[str]:
    """
    Find users assigned sensitive Entra directory roles.

    If role retrieval fails, execution stops rather than
    silently continuing with potentially dangerous behavior.
    """

    protected_user_ids: set[str] = set()

    try:
        role_definitions = get_role_definitions(client)

        assignments_path = (
            "/roleManagement/directory/"
            "roleAssignments"
            "?$select=id,principalId,roleDefinitionId"
            "&$top=100"
        )

        for assignment in client.pages(assignments_path):
            principal_id = assignment.get("principalId")
            role_definition_id = assignment.get(
                "roleDefinitionId"
            )

            if not principal_id:
                continue

            role_name = role_definitions.get(
                role_definition_id or ""
            )

            if role_name in PROTECTED_ROLE_NAMES:
                protected_user_ids.add(principal_id)

    except Exception as exc:
        raise RuntimeError(
            "Could not retrieve privileged role assignments. "
            "Stopping for safety. "
            f"Original error: {exc}"
        ) from exc

    return protected_user_ids


# ============================================================
# File hashing
# ============================================================

def calculate_file_sha256(
    path: Path,
) -> str:
    """Calculate SHA-256 hash for a file."""

    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


# ============================================================
# Phase 1 - Report
# ============================================================

def generate_report(
    recent_login_days: int = DEFAULT_RECENT_LOGIN_DAYS,
    password_length: int = DEFAULT_PASSWORD_LENGTH,
    report_path: Path | None = None,
    activity_check_enabled: bool = True,
) -> int:
    """
    Phase 1.

    READ ONLY against Entra.

    Generates a report containing:
      - users
      - RESET/SKIP decision
      - reason
      - temporary password for RESET users

    No passwords are changed.

    When activity_check_enabled=False, the script does not request
    signInActivity. This allows the workflow to operate in tenants
    without Entra ID Premium P1/P2.
    """

    print()
    print("=" * 70)
    print("PHASE 1 - ACCOUNT PASSWORD RESET REPORT")
    print("=" * 70)
    print()

    if activity_check_enabled:
        print(
            f"Recent login threshold : "
            f"{recent_login_days} days"
        )
        print(
            "Activity check          : ENABLED"
        )
    else:
        print(
            "Recent login threshold : NOT USED"
        )
        print(
            "Activity check          : DISABLED"
        )
        print(
            "Reason                  : "
            "signInActivity requires Entra ID Premium P1/P2"
        )

    print(
        f"Temporary password      : "
        f"{password_length} characters"
    )
    print()

    if report_path is None:
        report_path = get_report_path()

    REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    client = build_graph_client()

    print(
        "Getting protected administrator accounts..."
    )

    protected_user_ids = get_protected_user_ids(client)

    print(
        f"Protected accounts     : "
        f"{len(protected_user_ids)}"
    )

    print()

    rows: list[dict[str, Any]] = []

    reset_candidates = 0
    skipped = 0

    print("Scanning Entra users...")
    print()

    for user in get_users(
        client,
        activity_check_enabled=activity_check_enabled,
    ):
        user_id = user.get("id", "")
        display_name = user.get("displayName") or ""
        upn = user.get("userPrincipalName") or ""
        mail = user.get("mail") or ""

        # ----------------------------------------------------
        # Protected administrator
        # ----------------------------------------------------

        if user_id in protected_user_ids:
            action = "SKIP"
            reason = (
                "Protected privileged administrator account"
            )
            temporary_password = ""

        else:
            action, reason = classify_user(
                user,
                recent_login_days,
                activity_check_enabled=activity_check_enabled,
            )

            temporary_password = ""

            if action == "RESET":
                temporary_password = generate_password(
                    password_length
                )

        # ----------------------------------------------------
        # Login information
        # ----------------------------------------------------

        if activity_check_enabled:
            last_successful = get_last_successful_sign_in(user)
            last_successful_text = (
                last_successful.isoformat()
                if last_successful
                else ""
            )
        else:
            # Do not imply that the user has no sign-in history.
            last_successful_text = ""

        # ----------------------------------------------------
        # Counters
        # ----------------------------------------------------

        if action == "RESET":
            reset_candidates += 1
        else:
            skipped += 1

        # ----------------------------------------------------
        # Report row
        # ----------------------------------------------------

        rows.append(
            {
                "user_id": user_id,
                "display_name": display_name,
                "upn": upn,
                "mail": mail,
                "licensed": bool(
                    user.get("assignedLicenses")
                ),
                "account_enabled": user.get(
                    "accountEnabled"
                ),
                "user_type": user.get(
                    "userType",
                    "",
                ),
                "last_successful_sign_in": (
                    last_successful_text
                ),
                "activity_check_enabled": (
                    "true"
                    if activity_check_enabled
                    else "false"
                ),
                "action": action,
                "reason": reason,
                "temporary_password": (
                    temporary_password
                ),
                "force_change_password_next_sign_in": (
                    "true"
                    if action == "RESET"
                    else ""
                ),
            }
        )

    # ========================================================
    # Write CSV
    # ========================================================

    fields = [
        "user_id",
        "display_name",
        "upn",
        "mail",
        "licensed",
        "account_enabled",
        "user_type",
        "last_successful_sign_in",
        "activity_check_enabled",
        "action",
        "reason",
        "temporary_password",
        "force_change_password_next_sign_in",
    ]

    with report_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
        )

        writer.writeheader()
        writer.writerows(rows)

    # ========================================================
    # Hash report
    # ========================================================

    report_sha256 = calculate_file_sha256(report_path)

    # ========================================================
    # Write metadata
    # ========================================================

    generated_at = datetime.now(
        timezone.utc
    ).isoformat()

    meta_path = get_meta_path(report_path)

    meta_path.write_text(
        f"generated_at_utc={generated_at}\n"
        f"recent_login_days={recent_login_days}\n"
        f"password_length={password_length}\n"
        f"activity_check_enabled="
        f"{str(activity_check_enabled).lower()}\n"
        f"reset_candidates={reset_candidates}\n"
        f"skipped={skipped}\n"
        f"report_sha256={report_sha256}\n"
        f"phase=report-only\n"
        f"password_changes_made=false\n"
        "warning=This report contains temporary passwords. "
        "Protect it and never commit it to git.\n",
        encoding="utf-8",
    )

    # ========================================================
    # Summary
    # ========================================================

    print()
    print("=" * 70)
    print("REPORT COMPLETE")
    print("=" * 70)
    print()

    print(
        f"Reset candidates : "
        f"{reset_candidates}"
    )

    print(
        f"Skipped          : "
        f"{skipped}"
    )

    print()

    print(
        f"Report           : "
        f"{report_path}"
    )

    print(
        f"Metadata         : "
        f"{meta_path}"
    )

    print(
        f"SHA-256          : "
        f"{report_sha256}"
    )

    print()

    print(
        "IMPORTANT: No passwords were changed."
    )

    if not activity_check_enabled:
        print(
            "WARNING: Activity filtering was disabled because "
            "signInActivity requires Entra ID Premium P1/P2."
        )
        print(
            "RESET rows therefore mean eligible by license/account/"
            "user-type/privileged-role checks only."
        )

    print(
        "Review the CSV before running execute."
    )

    print()

    return 0


# ============================================================
# Phase 2 - Execute
# ============================================================

def execute_report(
    report_path: Path | None = None,
) -> int:
    """
    Phase 2.

    Reads the exact report produced by Phase 1.

    Only rows with:
        action = RESET

    are processed.

    Before execution:
      - report must exist
      - metadata must exist
      - SHA-256 must match
      - explicit RESET confirmation is required
    """

    print()
    print("=" * 70)
    print("PHASE 2 - EXECUTE PASSWORD RESET")
    print("=" * 70)
    print()

    if report_path is None:
        report_path = get_report_path()

    meta_path = get_meta_path(report_path)
    result_path = get_result_path(report_path)

    # --------------------------------------------------------
    # Report existence
    # --------------------------------------------------------

    if not report_path.exists():
        raise SystemExit(
            "\nReport not found.\n\n"
            "Run Phase 1 first:\n\n"
            "  python -m migration.account_password report\n"
        )

    if not meta_path.exists():
        raise SystemExit(
            "\nReport metadata not found.\n\n"
            "For safety, generate a new report before execute.\n"
        )

    print(
        f"Using report: {report_path}"
    )
    print()

    # --------------------------------------------------------
    # Verify report integrity
    # --------------------------------------------------------

    expected_hash = None

    for line in meta_path.read_text(
        encoding="utf-8"
    ).splitlines():
        if line.startswith("report_sha256="):
            expected_hash = line.split(
                "=",
                1,
            )[1].strip()
            break

    if not expected_hash:
        raise SystemExit(
            "\nMetadata does not contain report_sha256.\n"
            "For safety, generate a new report.\n"
        )

    actual_hash = calculate_file_sha256(
        report_path
    )

    if actual_hash != expected_hash:
        print("REPORT INTEGRITY CHECK FAILED")
        print()
        print(
            f"Expected SHA-256 : {expected_hash}"
        )
        print(
            f"Actual SHA-256   : {actual_hash}"
        )
        print()

        raise SystemExit(
            "The report has been modified since it was generated. "
            "Execution stopped."
        )

    print(
        "Report integrity : OK"
    )
    print()

    # --------------------------------------------------------
    # Read report
    # --------------------------------------------------------

    with report_path.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        rows = list(
            csv.DictReader(handle)
        )

    reset_rows = [
        row
        for row in rows
        if row.get("action") == "RESET"
    ]

    if not reset_rows:
        print(
            "No RESET users found in the report."
        )
        return 0

    # --------------------------------------------------------
    # Summary before confirmation
    # --------------------------------------------------------

    print(
        f"Users to process: "
        f"{len(reset_rows)}"
    )

    print()

    print(
        "The following users will have their passwords changed:"
    )

    print()

    for row in reset_rows:
        print(
            f"  - {row.get('upn', '')}"
        )

    print()

    print(
        "Temporary passwords are stored in the report."
    )

    print(
        "They will NOT be displayed here."
    )

    print()

    # --------------------------------------------------------
    # Confirmation
    # --------------------------------------------------------

    confirmation = input(
        "Type RESET to continue: "
    ).strip()

    if confirmation != "RESET":
        print()
        print(
            "Execution cancelled."
        )
        return 0

    print()

    # --------------------------------------------------------
    # Authentication
    # --------------------------------------------------------

    client = build_graph_client()

    applied = 0
    failed = 0

    result_rows: list[dict[str, str]] = []

    # ========================================================
    # Apply passwords
    # ========================================================

    for index, row in enumerate(
        reset_rows,
        start=1,
    ):
        user_id = row.get(
            "user_id",
            "",
        )

        upn = row.get(
            "upn",
            "",
        )

        password = row.get(
            "temporary_password",
            "",
        )

        print(
            f"[{index}/{len(reset_rows)}] "
            f"{upn}"
        )

        # ----------------------------------------------------
        # Validate row
        # ----------------------------------------------------

        if not user_id:
            print(
                "  [ERROR] Missing user ID"
            )

            failed += 1

            result_rows.append(
                {
                    "user_id": user_id,
                    "upn": upn,
                    "status": "FAILED",
                    "detail": "Missing user ID",
                }
            )

            continue

        if not password:
            print(
                "  [ERROR] Missing temporary password"
            )

            failed += 1

            result_rows.append(
                {
                    "user_id": user_id,
                    "upn": upn,
                    "status": "FAILED",
                    "detail": (
                        "Missing temporary password"
                    ),
                }
            )

            continue

        # ----------------------------------------------------
        # Apply password
        # ----------------------------------------------------

        try:
            client.request(
                "PATCH",
                f"/users/{user_id}",
                json={
                    "passwordProfile": {
                        "password": password,
                        "forceChangePasswordNextSignIn": True,
                    }
                },
            )

            print(
                "  [OK] Password updated"
            )

            print(
                "  [OK] Force password change enabled"
            )

            applied += 1

            result_rows.append(
                {
                    "user_id": user_id,
                    "upn": upn,
                    "status": "SUCCESS",
                    "detail": (
                        "Password updated successfully"
                    ),
                }
            )

        except Exception as exc:
            print(
                f"  [ERROR] {exc}"
            )

            failed += 1

            result_rows.append(
                {
                    "user_id": user_id,
                    "upn": upn,
                    "status": "FAILED",
                    "detail": str(exc),
                }
            )

    # ========================================================
    # Write execution result
    # ========================================================

    with result_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "user_id",
                "upn",
                "status",
                "detail",
            ],
        )

        writer.writeheader()
        writer.writerows(result_rows)

    # ========================================================
    # Summary
    # ========================================================

    print()
    print("=" * 70)
    print("EXECUTION COMPLETE")
    print("=" * 70)
    print()

    print(
        f"Applied : {applied}"
    )

    print(
        f"Failed  : {failed}"
    )

    print()

    print(
        f"Result  : {result_path}"
    )

    print()

    if failed:
        print(
            "Some users failed."
        )

        print(
            "Review the execution result before retrying."
        )

        print()

        return 1

    print(
        "All selected users were processed successfully."
    )

    return 0


# ============================================================
# CLI
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Microsoft Entra account password "
            "report and reset workflow."
        )
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    # --------------------------------------------------------
    # report
    # --------------------------------------------------------

    report_parser = subparsers.add_parser(
        "report",
        help=(
            "Generate a read-only report "
            "with temporary passwords."
        ),
    )

    report_parser.add_argument(
        "--recent-days",
        type=int,
        default=DEFAULT_RECENT_LOGIN_DAYS,
        help=(
            "Skip users whose last successful "
            "login was within this many days. "
            "Ignored when --no-activity-check is used."
        ),
    )

    report_parser.add_argument(
        "--no-activity-check",
        action="store_true",
        help=(
            "Do not request signInActivity. "
            "Use this for tenants without Entra ID Premium P1/P2. "
            "The report will not claim that users are inactive."
        ),
    )

    report_parser.add_argument(
        "--password-length",
        type=int,
        default=DEFAULT_PASSWORD_LENGTH,
        help=(
            "Temporary password length."
        ),
    )

    report_parser.add_argument(
        "--report",
        default=None,
        help=(
            "Optional report filename under reports/."
        ),
    )

    # --------------------------------------------------------
    # execute
    # --------------------------------------------------------

    execute_parser = subparsers.add_parser(
        "execute",
        help=(
            "Apply passwords from the reviewed report."
        ),
    )

    execute_parser.add_argument(
        "--report",
        default=None,
        help=(
            "Optional report filename under reports/."
        ),
    )

    args = parser.parse_args()

    # --------------------------------------------------------
    # report
    # --------------------------------------------------------

    if args.command == "report":
        report_path = get_report_path(
            args.report
        )

        return generate_report(
            recent_login_days=args.recent_days,
            password_length=args.password_length,
            report_path=report_path,
            activity_check_enabled=(
                not args.no_activity_check
            ),
        )

    # --------------------------------------------------------
    # execute
    # --------------------------------------------------------

    if args.command == "execute":
        report_path = get_report_path(
            args.report
        )

        return execute_report(
            report_path=report_path
        )

    return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )

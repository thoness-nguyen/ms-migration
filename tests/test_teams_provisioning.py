from __future__ import annotations

from migration.common.checkpoint import StateStore
from migration.teams import provision_targets
from migration.teams.provision_targets import finalize, provision

UNRESOLVED = [
    {"team": "HO.HK", "name": "General", "source_team_id": "src-team", "source_channel_id": "src-general"},
    {"team": "HO.HK", "name": "STOCK CHINA", "source_team_id": "src-team", "source_channel_id": "src-stock"},
    {"team": "HO.HK", "name": "Contracts", "source_team_id": "src-team", "source_channel_id": "src-private"},
]


class Source:
    def __init__(self, primary="src-general"):
        self.primary = primary

    def request(self, method, path, **kwargs):
        assert method == "GET"
        if path.endswith("/primaryChannel?$select=id"):
            return {"id": self.primary}
        return {"displayName": "HO.HK", "description": "HK", "createdDateTime": "2021-05-01T00:00:00Z"}

    def pages(self, path):
        if path.startswith("/teams/src-team/channels"):
            return iter([
                {"id": "src-general", "displayName": "General", "membershipType": "standard", "createdDateTime": "2021-05-01T00:00:00Z"},
                {"id": "src-stock", "displayName": "STOCK CHINA", "membershipType": "standard", "createdDateTime": "2020-01-01T00:00:00Z"},
                {"id": "src-private", "displayName": "Contracts", "membershipType": "private", "createdDateTime": "2022-01-01T00:00:00Z"},
            ])
        if path.startswith("/groups/src-team/owners"):
            return iter([{"id": "s-owner"}])
        if path.startswith("/groups/src-team/members"):
            return iter([{"id": "s-owner"}, {"id": "s-member"}, {"id": "s-unmapped"}])
        return iter([])


class Response:
    status_code = 202
    text = ""
    headers = {"Content-Location": "/teams('new-team')/operations('op')"}


class Target:
    base_url = "https://graph.microsoft.com/v1.0"

    def __init__(self, existing_team=None):
        self.existing_team = existing_team
        self.writes: list[tuple[str, str, dict]] = []

    def pages(self, path):
        if path.startswith("/teams?"):
            teams = [{"id": "other", "displayName": "Paizes Harbour PD Work Flow"}]
            if self.existing_team:
                teams.append({"id": self.existing_team, "displayName": "ho.hk"})
            return iter(teams)
        if path.endswith("/channels?$select=id,displayName"):
            return iter([{"id": "tgt-general", "displayName": "General"}])
        return iter([])

    def request(self, method, path, **kwargs):
        if method != "GET":
            self.writes.append((method, path, kwargs.get("json")))
        if path.endswith("/primaryChannel?$select=id"):
            return {"id": "tgt-general"}
        if method == "POST" and path.endswith("/channels"):
            return {"id": "tgt-stock"}
        return {}

    def raw_request(self, method, url, **kwargs):
        self.writes.append((method, url, kwargs.get("json")))
        return Response()


def test_dry_run_makes_no_writes():
    target = Target()
    channels, skipped, _ = provision(Source(), target, UNRESOLVED, apply=False)
    assert target.writes == []
    assert channels == []  # nothing exists yet, nothing created
    assert [s["source_channel_id"] for s in skipped] == ["src-private"]


def test_apply_creates_team_and_channels_in_migration_mode(monkeypatch):
    monkeypatch.setattr(provision_targets, "_wait_for_team", lambda target, team_id: None)
    target = Target()
    channels, skipped, _ = provision(Source(), target, UNRESOLVED, apply=True)

    team_post = next(w for w in target.writes if w[1].endswith("/teams"))
    assert team_post[2]["@microsoft.graph.teamCreationMode"] == "migration"
    # team must predate every channel it will hold (STOCK CHINA is older than the group)
    assert team_post[2]["createdDateTime"] == "2020-01-01T00:00:00Z"

    channel_post = next(w for w in target.writes if w[1] == "/teams/new-team/channels")
    assert channel_post[2]["@microsoft.graph.channelCreationMode"] == "migration"
    assert channel_post[2]["membershipType"] == "standard"
    assert channel_post[2]["createdDateTime"] == "2020-01-01T00:00:00Z"

    assert {(c["source_channel_id"], c["target_channel_id"]) for c in channels} == {("src-general", "tgt-general"), ("src-stock", "tgt-stock")}
    assert all(c["target_team_id"] == "new-team" for c in channels)
    # private channel is never provisioned as standard
    assert [s["source_channel_id"] for s in skipped] == ["src-private"]
    assert not any(w[2] and w[2].get("displayName") == "Contracts" for w in target.writes)


def test_renamed_primary_channel_maps_to_target_general_not_a_new_channel(monkeypatch):
    """A source team's primary channel can be renamed (e.g. 'EU Group') - it must
    land in the target team's General channel, not a duplicate channel."""
    monkeypatch.setattr(provision_targets, "_wait_for_team", lambda target, team_id: None)
    target = Target()
    renamed = [{"team": "HO.HK", "name": "STOCK CHINA", "source_team_id": "src-team", "source_channel_id": "src-stock"}]
    channels, _, _ = provision(Source(primary="src-stock"), target, renamed, apply=True)
    assert not any(w[1] == "/teams/new-team/channels" for w in target.writes)
    assert channels[0]["target_channel_id"] == "tgt-general"


def test_existing_target_team_is_reused_not_recreated():
    target = Target(existing_team="existing-team")
    channels, _, _ = provision(Source(), target, UNRESOLVED, apply=True)
    assert not any(w[1].endswith("/teams") for w in target.writes)
    assert {c["target_team_id"] for c in channels} == {"existing-team"}


def test_finalize_refuses_until_all_channels_completed(tmp_path):
    state = StateStore(tmp_path / "teams-checkpoint.sqlite")
    provisioned = [
        {"name": "a", "source_team_id": "src-team", "source_channel_id": "c1", "target_team_id": "new-team", "target_channel_id": "t1"},
        {"name": "b", "source_team_id": "src-team", "source_channel_id": "c2", "target_team_id": "new-team", "target_channel_id": "t2"},
    ]
    state.mark("batch-channel", "src-team:c1", "completed")
    target = Target()
    finalize(Source(), target, provisioned, state, {"s-owner": "t-owner"}, apply=True)
    assert target.writes == []

    state.mark("batch-channel", "src-team:c2", "completed")
    done = finalize(Source(), target, provisioned, state, {"s-owner": "t-owner", "s-member": "t-member"}, apply=True)
    assert done == ["new-team"]
    # every channel (incl. General) is completed before the team - Graph rejects
    # team completeMigration otherwise ("General channel must be finalized before team")
    paths = [w[1] for w in target.writes]
    assert paths[:2] == ["/teams/new-team/channels/tgt-general/completeMigration", "/teams/new-team/completeMigration"]
    # same owners/members as the source group; unmapped source user reported, not guessed
    members = sorted(w[2]["@odata.id"].rsplit("/", 1)[-1] for w in target.writes if w[1] == "/groups/new-team/members/$ref")
    owners = [w[2]["@odata.id"].rsplit("/", 1)[-1] for w in target.writes if w[1] == "/groups/new-team/owners/$ref"]
    assert members == ["t-member", "t-owner"] and owners == ["t-owner"]


def test_site_only_team_is_created_without_extra_channels_and_finalizes_immediately(tmp_path, monkeypatch):
    monkeypatch.setattr(provision_targets, "_wait_for_team", lambda target, team_id: None)
    target = Target()
    channels, _, teams = provision(Source(), target, [], apply=True, site_team_ids=["src-team"])
    assert channels == []
    assert teams == [{"name": "HO.HK", "source_team_id": "src-team", "target_team_id": "new-team", "created_by_script": True}]
    assert not any(w[1] == "/teams/new-team/channels" for w in target.writes)
    finalize(Source(), target, [], StateStore(tmp_path / "s.sqlite"), {"s-owner": "t-owner"}, apply=True, teams=teams)
    assert ("POST", "/teams/new-team/completeMigration", None) in target.writes


def test_established_pair_is_reused_and_never_finalized(tmp_path):
    """Harbour PD Work Flow -> 'Paizes Harbour PD Work Flow' (different names)."""
    target = Target()
    _, _, teams = provision(Source(), target, UNRESOLVED, apply=True, known_team_map={"src-team": "paizes-harbour"})
    assert not any(w[1].endswith("/teams") for w in target.writes)
    assert teams[0]["target_team_id"] == "paizes-harbour" and teams[0]["created_by_script"] is False
    finalize(Source(), target, [], StateStore(tmp_path / "s.sqlite"), {}, apply=True, teams=teams)
    assert not any("completeMigration" in w[1] for w in target.writes)


def test_site_only_team_completes_general_channel_before_team(tmp_path):
    target = Target()
    teams = [{"name": "PG", "source_team_id": "src-team", "target_team_id": "new-team", "created_by_script": True}]
    finalize(Source(), target, [], StateStore(tmp_path / "s.sqlite"), {"s-owner": "t-owner"}, apply=True, teams=teams)
    paths = [w[1] for w in target.writes]
    assert paths.index("/teams/new-team/channels/tgt-general/completeMigration") < paths.index("/teams/new-team/completeMigration")


def test_finalize_continues_after_one_team_fails_and_tolerates_already_completed(tmp_path):
    from migration.common.graph import GraphError

    class FlakyTarget(Target):
        def request(self, method, path, **kwargs):
            if method == "POST" and path == "/teams/bad-team/completeMigration":
                raise GraphError("POST failed (400): General channel must be finalized before team.", status_code=400)
            if method == "POST" and path == "/teams/good-team/channels/tgt-general/completeMigration":
                raise GraphError("POST failed (400): Channel has already completed migration.", status_code=400)
            return super().request(method, path, **kwargs)

    target = FlakyTarget()
    teams = [
        {"name": "bad", "source_team_id": "src-team", "target_team_id": "bad-team", "created_by_script": True},
        {"name": "good", "source_team_id": "src-team", "target_team_id": "good-team", "created_by_script": True},
    ]
    done = finalize(Source(), target, [], StateStore(tmp_path / "s.sqlite"), {"s-owner": "t-owner"}, apply=True, teams=teams)
    assert done == ["good-team"]
    # failed team gets no members (it is still in migration mode)
    assert not any(w[1].startswith("/groups/bad-team/") for w in target.writes)
    assert any(w[1] == "/groups/good-team/owners/$ref" for w in target.writes)

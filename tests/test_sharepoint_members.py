from migration.sharepoint.sync_members import sync_group


class Source:
    def pages(self, path):
        if path.startswith("/groups/sg/owners"):
            return iter([{"id": "so"}])
        if path.startswith("/groups/sg/members"):
            return iter([{"id": "so"}, {"id": "sm"}, {"id": "sx"}, {"id": "su"}])
        return iter([])


class Target:
    def __init__(self):
        self.calls = []

    def pages(self, path):
        if path.startswith("/groups/tg/owners"):
            return iter([{"id": "placeholder-admin"}])
        if path.startswith("/groups/tg/members"):
            return iter([{"id": "placeholder-admin"}, {"id": "tm"}])
        return iter([])

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, (kwargs.get("json") or {}).get("@odata.id", "").rsplit("/", 1)[-1]))
        return {}


MAP = {"so": "to", "sm": "tm"}


def test_dry_run_reports_without_writing():
    target = Target()
    report = sync_group(Source(), target, "sg", "tg", MAP, {"sx"}, apply=False)
    assert target.calls == []
    assert (report["owners"], report["members"], report["unmapped"], report["excluded"]) == (1, 2, ["su"], ["sx"])


def test_additive_by_default_keeps_placeholder_owner():
    target = Target()
    sync_group(Source(), target, "sg", "tg", MAP, {"sx"}, apply=True)
    assert ("POST", "/groups/tg/members/$ref", "to") in target.calls  # owner is also a member
    assert ("POST", "/groups/tg/owners/$ref", "to") in target.calls
    assert not any(method == "POST" and who == "tm" for method, _, who in target.calls)  # already a member
    assert not any(method == "DELETE" for method, _, _ in target.calls)


def test_remove_extra_owners_removes_placeholder():
    target = Target()
    report = sync_group(Source(), target, "sg", "tg", MAP, set(), apply=True, remove_extra_owners=True)
    assert ("DELETE", "/groups/tg/owners/placeholder-admin/$ref", "") in target.calls
    assert report["removed_owners"] == 1

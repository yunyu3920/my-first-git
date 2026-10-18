"""上架成功后清理店小秘图片空间：只删这次传的、锁定的不删。"""

from __future__ import annotations

from temu_bot.web.album_clean import clean_uploaded_images


class FakeClient:
    def __init__(self, items, code=0):
        self.items = items
        self.code = code
        self.deleted: list[str] | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def album_find_by_urls(self, urls):
        return self.items

    def album_delete(self, ids):
        self.deleted = list(ids)
        return {"code": self.code, "msg": "ok" if self.code == 0 else "删不掉"}


def run_with(sandbox, fake, urls=("https://wxalbum.example.com/wxalbum/1/a.jpg",)):
    return clean_uploaded_images(
        sandbox["cfg"], "hat-tshirt", list(urls), client_factory=lambda *a, **k: fake
    )


def test_deletes_only_unlocked_images(sandbox):
    fake = FakeClient(
        [
            {"idStr": "1", "lockState": 0},
            {"idStr": "2", "lockState": 1},       # 锁定的跳过
            {"idStr": "3", "lockState": "0"},
        ]
    )
    result = run_with(sandbox, fake)

    assert result.found == 3
    assert result.deleted == 2
    assert result.skipped == 1
    assert fake.deleted == ["1", "3"]
    assert result.ok is True


def test_no_urls_means_no_call(sandbox):
    fake = FakeClient([])
    result = run_with(sandbox, fake, urls=())
    assert (result.found, result.deleted) == (0, 0)
    assert fake.deleted is None


def test_nothing_found_is_not_an_error(sandbox):
    result = run_with(sandbox, FakeClient([]))
    assert result.found == 0
    assert result.deleted == 0
    assert result.ok is True


def test_delete_failure_is_reported_not_raised(sandbox):
    result = run_with(sandbox, FakeClient([{"idStr": "1", "lockState": 0}], code=-1))
    assert result.deleted == 0
    assert "删不掉" in result.error


def test_without_login_it_refuses(sandbox):
    result = clean_uploaded_images(
        sandbox["cfg"], "", ["https://x/a.jpg"], client_factory=lambda *a, **k: FakeClient([])
    )
    assert result.error
    assert result.deleted == 0

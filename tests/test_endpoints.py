"""接口端点注册表：默认值、配置覆盖、候选端点自动换。"""

from __future__ import annotations

from temu_bot.dianxiaomi.client import DxmClient, SiteChanged
from temu_bot.dianxiaomi.endpoints import DEFAULT_ENDPOINTS, EndpointRegistry, load_registry


def test_defaults_include_all_core_ops():
    reg = EndpointRegistry()
    for op in ("user_in", "product_list", "product_edit", "copy", "save", "album_delete"):
        assert reg.paths(op), op

    # 图片空间删除有两个候选（新旧接口都见过）
    assert reg.paths("album_delete")[0] == "/api/album/delPic.json"
    assert "/api/cos/deletePic.json" in reg.paths("album_delete")
    # 保存接口的 token 路径要保持原样
    assert reg.paths("save")[0].endswith("/add3f8c2a97.json")


def test_overrides_replace_only_named_ops():
    reg = EndpointRegistry(
        {"copy": ["/api/new/copy.json"], "album_delete": "/api/x.json"}
    )
    assert reg.paths("copy") == ["/api/new/copy.json"]
    assert reg.paths("album_delete") == ["/api/x.json"]
    # 没覆盖的保持默认
    assert reg.paths("save") == list(DEFAULT_ENDPOINTS["save"])
    assert "copy" in reg.overridden


def test_bad_config_falls_back_to_defaults(tmp_path, sandbox):
    cfg = sandbox["cfg"]
    cfg.root = tmp_path
    path = tmp_path / "config" / "dxm_endpoints.json"
    path.parent.mkdir()
    path.write_text("{ 这不是 json", encoding="utf-8")

    reg = load_registry(cfg)
    assert reg.paths("user_in") == ["/api/userIn.json"]
    assert reg.overridden == set()


def test_missing_config_uses_defaults(tmp_path, sandbox):
    cfg = sandbox["cfg"]
    cfg.root = tmp_path
    assert load_registry(cfg).paths("copy") == ["/api/banjia/copyProduct.json"]


def test_invalid_paths_are_ignored():
    reg = EndpointRegistry({"copy": "not-a-path", "save": [123, "also-bad"]})
    assert reg.paths("copy") == list(DEFAULT_ENDPOINTS["copy"])
    assert reg.paths("save") == list(DEFAULT_ENDPOINTS["save"])


def test_call_paths_tries_the_next_candidate_on_404():
    client = object.__new__(DxmClient)
    client.endpoints = EndpointRegistry({"album_delete": ["/a.json", "/b.json"]})
    seen: list[str] = []

    def fetch(path: str):
        seen.append(path)
        if path == "/a.json":
            raise SiteChanged("HTTP 404")
        return {"ok": path}

    assert client._call_paths("album_delete", "删除图片", fetch) == {"ok": "/b.json"}
    assert seen == ["/a.json", "/b.json"]


def test_call_paths_does_not_swallow_other_errors():
    client = object.__new__(DxmClient)
    client.endpoints = EndpointRegistry({"copy": ["/a.json", "/b.json"]})

    def fetch(path: str):
        raise RuntimeError("别的错")

    try:
        client._call_paths("copy", "复制", fetch)
    except RuntimeError as exc:
        assert "别的错" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应该原样抛出")

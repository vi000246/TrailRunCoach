"""SP-946 (SP-844 Q1 option A): the 百岳 list's provenance is written down.

  - the About page lists 百岳與小百岳 in its data sources, in Chinese and English: the points from
    happyman's Map Generator (a link to twmap.happyman.idv.tw), the 100 Peaks list by the 山岳協會
  - backend/data/baiyue.json says the same in its `source`, and so does the build script (a rebuild keeps it)
  - a README next to the file tells where it comes from
Synthetic data only (the page is rendered by the app; the data file is the shipped one)."""
from __future__ import annotations

import json
from pathlib import Path

from backend.api import about as A
from backend.scripts import build_baiyue as B
from backend.tests.test_about_page_sp495 import _cat, _text, owner  # noqa: F401 — fixture used by name
from backend.tests.test_login_sp58 import HTML

DATA = Path(__file__).resolve().parents[1] / "data"
LINE = "百岳與小百岳點位：地圖產生器（happyman，twmap.happyman.idv.tw）；百岳名單：中華民國山岳協會"


def test_the_data_list_has_the_baiyue_entry_with_happymans_site():
    links = dict(A.DATA)["baiyue"]
    assert links == (("twmap.happyman.idv.tw", "https://twmap.happyman.idv.tw/"),)
    assert LINE in _cat("zh-TW")["d.baiyue.desc"]
    en = _cat("en")["d.baiyue.desc"]
    assert "Map Generator by happyman (twmap.happyman.idv.tw)" in en
    assert "Chinese Taipei Mountaineering Association" in en


def test_the_about_page_shows_it_in_both_languages(owner):  # noqa: F811
    zh = owner.get("/about?lang=zh-TW", headers=HTML)
    en = owner.get("/about?lang=en", headers=HTML)
    assert zh.status_code == en.status_code == 200
    for r in (zh, en):
        assert '<li class="ab-item" id="d-baiyue">' in r.text
        assert '<a href="https://twmap.happyman.idv.tw/" target="_blank" rel="noopener">twmap.happyman.idv.tw</a>' \
            in r.text
    assert "百岳與小百岳" in _text(zh.text) and LINE in _text(zh.text)
    t = _text(en.text)
    assert "Taiwan's 100 Peaks (Baiyue) and Little 100 Peaks" in t.replace("&#x27;", "'").replace("&#39;", "'")
    assert "Chinese Taipei Mountaineering Association" in t


def test_the_data_file_and_its_build_script_carry_the_source():
    data = json.loads((DATA / "baiyue.json").read_text("utf-8"))
    assert data["source"] == B.SOURCE == LINE
    assert data["rows"] == B.ROWS
    # a rebuild writes the same provenance (synthetic rows: 100 百岳 + one 小百岳 + a hot spring)
    rows = [(f"山{i}", 3000.0 + i, 121.0, 24.0, 1) for i in range(100)] + [("小山", 500.0, 121.5, 25.0, 2),
                                                                          ("溫泉", 800.0, 121.0, 24.0, 8)]
    built = B.build(rows)
    assert built["source"] == LINE and built["rows"] == B.ROWS
    assert built["count"] == {"baiyue": 100, "xiaobaiyue": 1}
    assert "twmap.happyman.idv.tw" in B.__doc__ and "中華民國山岳協會" in B.__doc__


def test_a_readme_next_to_the_file_tells_where_it_comes_from():
    readme = (DATA / "README.md").read_text("utf-8")
    assert "baiyue.json" in readme and LINE in readme
    assert "https://twmap.happyman.idv.tw/gen/api/pointdata.php?id=ALL" in readme

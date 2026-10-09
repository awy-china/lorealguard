"""Agent 闭环守卫 —— 「识别 → 判定 → 预警 → 建议」四环每环都必须能被证伪。

最要紧的两条（都是本项目的头号纪律）：
    ① **弃权 ≠ 无异常**：判不了要说"判不了"，没问题要说"没问题"，两者不许混；
    ② **预警必须说清是哪一层的形态**：图类用例印出「评论区批量注水」= 张冠李戴，
       这种错在答辩现场是致命的，所以做成测试。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voiceguard.agent import advise                                  # noqa: E402
from voiceguard.core.contract import ContentItem                     # noqa: E402
from voiceguard.guard import analyze_content                         # noqa: E402

OUT = "output/_agent_test"

BOT = ["姐妹们这个真的好用到爆，我用了一周皮肤绝了，私信我领券",
       "姐妹们这个真的好用到爆，我用了一周皮肤绝了～私信我领券",
       "姐妹们这个真的好用到爆，我用了一周皮肤绝了，私信我领券！",
       "姐妹们这个真的好用到爆，我用了一周皮肤绝了，私信我领券",
       "姐妹们这个真的好用到爆，我用了一周皮肤绝了，私信我领券～",
       "姐妹们这个真的好用到爆，我用了一周皮肤绝了",
       "私信我发你内部渠道价，比专柜便宜一半"]
BENIGN = ["30ml 用了两周，混油皮夏天有点闷，但早上上妆不搓泥。",
          "我是干皮，换季用还行，就是香味有点重，个人感受。",
          "回购第二瓶了，配合烟酰胺精华用，效果一般般。",
          "质地挺薄的，空调房里需要叠涂，仅供参考。",
          "用了 5 天没什么感觉，可能我皮太稳了。"]
TOO_FEW = ["好用", "已回购"]

BANNED_PATTERNS = (r"是水军", r"(建议|应当|应予|必须)处罚", r"封号", r"删除评论")


@pytest.fixture(scope="module")
def decisions():
    cases = {
        "bot": ContentItem(meta={"id": "adv01"}, comments=BOT, kind="comment_thread"),
        "benign": ContentItem(meta={"id": "adv02"}, comments=BENIGN, kind="comment_thread"),
        "too_few": ContentItem(meta={"id": "adv03"}, comments=TOO_FEW, kind="comment_thread"),
        "image": ContentItem(meta={"id": "adv04"}, image_path="samples/fw_splice.jpg",
                             kind="image", text="7天根除痘印，使用前后对比，链接在评论区"),
    }
    out = {}
    for name, item in cases.items():
        rep = analyze_content(item, outdir=OUT, tag=item.meta["id"], sheet=False)
        out[name] = (rep, advise(rep))
    return out


def test_abstention_is_not_confused_with_no_anomaly(decisions):
    """① 弃权与「未触发」必须印出不同的话 —— 混了就是把'判不了'说成'没问题'。"""
    _, d_bot = decisions["bot"]
    _, d_benign = decisions["benign"]
    _, d_few = decisions["too_few"]

    assert d_bot.tier == "高风险"
    assert "注水" in d_bot.alert
    assert "弃权" in d_few.alert, f"条数不足却没说弃权：{d_few.alert}"
    assert d_few.abstain_reasons, "弃权必须带归因原因"
    assert "弃权" not in d_benign.alert, f"良性内容被印成弃权：{d_benign.alert}"
    assert "不等于「已确认为真实」" in d_benign.alert, "未触发档必须声明它不是「判定为真」"


def test_alert_names_the_right_layer(decisions):
    """② 预警不许张冠李戴：图类用例不能印评论区的形态描述。"""
    _, d_img = decisions["image"]
    assert "评论区" not in d_img.alert, f"图类用例的预警里出现了评论区措辞：{d_img.alert}"
    assert "拼接" in d_img.alert or "功效" in d_img.alert, f"图类预警没指出自己的形态：{d_img.alert}"
    _, d_bot = decisions["bot"]
    assert "评论区" in d_bot.alert


def test_every_decision_has_both_sides_and_human_in_the_loop(decisions):
    """③ 每个等级都必须同时给平台侧与创作者侧建议，并写清人在环上的位置。"""
    for name, (_, d) in decisions.items():
        owners = {a.get("owner") for a in d.actions}
        assert owners == {"平台", "创作者"}, f"{name} 的建议没覆盖双方：{owners}"
        assert d.human_in_the_loop.strip(), f"{name} 没写人在环上的位置"
        assert d.disclaimer.strip(), f"{name} 没写免责声明"
        assert any(a.get("requires_human") for a in d.actions) == (d.tier in {"高风险", "关注级"} or
                                                                   "弃权" in d.alert), \
            f"{name} 的「需人工」标记与等级不匹配"


def test_decision_text_has_no_verdict_or_punishment_language(decisions):
    for name, (_, d) in decisions.items():
        blob = " ".join([d.alert, d.human_in_the_loop, d.disclaimer]
                        + [a.get("action", "") for a in d.actions])
        for pat in BANNED_PATTERNS:
            assert not re.search(pat, blob), f"{name} 的决策文本出现终审/处置主张 /{pat}/"


def test_playbook_every_tier_has_actions():
    """④ 加档不加建议 = 空档。这条防的是后人往 playbook 里塞等级却忘写建议。"""
    from voiceguard.rules.engine import load_rules

    pb = load_rules()["agent_playbook"]
    for tier, block in pb["tiers"].items():
        assert block.get("actions"), f"档位 {tier} 没有处置建议"
        assert block.get("alert"), f"档位 {tier} 没有预警措辞"
        assert {a.get("owner") for a in block["actions"]} == {"平台", "创作者"}
    assert pb["abstain"]["actions"] and pb["abstain"]["alert"]
    assert pb["alert_by_prefix"], "预警分流表不能为空 —— 否则又回到万能话术"
    assert pb["human_checkpoints"], "必须写清哪些环节必须人工"
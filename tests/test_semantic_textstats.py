"""F4-B 文案统计信号测试 —— 商业语境判定 + 词典的误报护栏。

词典纪律：只收**明确商业符号**（URL / 商品域 / 购买引导语 / 广告声明词）。
「安利 / 推荐 / 种草 / 回购」这类日常用语一律**不收** —— 收进来就等于把
普通用户的真情实感当软广，那正是本项目要防的误伤。
"""
from __future__ import annotations

from voiceguard.semantic import textstats


def test_detects_shopping_link_and_ad_label():
    s = textstats.analyze("亲测好用，链接在评论区 #广告")
    assert s.has_shopping_link is True
    assert s.ad_label_declared is True
    sig = s.signals()
    assert sig["text.has_shopping_link"] is True and sig["text.ad_label_declared"] is True


def test_detects_url_and_platform_domain():
    for t in ("详情 https://item.taobao.com/x", "购物车已挂", "点击购买", "小黄车已挂"):
        assert textstats.analyze(t).has_shopping_link is True, t


def test_platform_name_alone_is_not_commerce():
    """刻意的保守：光提平台名不算商业引导（「淘宝买的挺好」是正常分享）。"""
    for t in ("淘宝搜店铺", "京东自营买的", "拼多多上很便宜"):
        assert textstats.analyze(t).has_shopping_link is False, t


def test_vague_words_are_not_commerce_signals():
    """误报护栏：口语化的推荐词不是商业符号。"""
    for t in ("真心安利给大家", "我会回购的", "种草很久了", "推荐给姐妹"):
        s = textstats.analyze(t)
        assert s.has_shopping_link is False, f"把日常用语当商业推广 → 冤枉：{t}"
        assert s.ad_label_declared is False, t


def test_ad_label_requires_explicit_marker():
    assert textstats.analyze("品牌赞助").ad_label_declared is True
    assert textstats.analyze("合作推广").ad_label_declared is True
    assert textstats.analyze("我自己买的，没接广告")  # 含「广告」二字 → 属已声明，宁可放过
    assert textstats.analyze("最近皮肤状态不错").ad_label_declared is False


def test_before_after_context():
    assert textstats.analyze("使用前后对比，第7天打卡").before_after_context is True
    assert textstats.analyze("素颜也能打，实测三天").before_after_context is True
    assert textstats.analyze("今天天气不错，出门散步").before_after_context is False


def test_signals_quote_original_text():
    """可解释性的最小单位是「引用原句」：每条命中都要带出原文片段。"""
    s = textstats.analyze("#广告 链接在评论区")
    all_hits = [h for v in s.hits.values() for h in v]
    assert all_hits, "应至少有一处命中"
    for h in all_hits:
        assert "：" in h, f"命中项必须写成『标签：原文片段』，便于报告层直接引用：{h}"


def test_empty_text_is_silent():
    s = textstats.analyze("")
    assert s.has_shopping_link is False and s.ad_label_declared is False
    assert s.before_after_context is False
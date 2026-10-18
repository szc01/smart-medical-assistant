from app.rag.context import wants_report_value


def test_wants_report_value_hit() -> None:
    assert wants_report_value("我报告里的空腹血糖是多少") is True
    assert wants_report_value("上次体检的 ALT 指标是多少") is True


def test_wants_report_value_miss() -> None:
    assert wants_report_value("感冒了能吃什么") is False
    assert wants_report_value("这个药多少钱") is False
    assert wants_report_value("") is False

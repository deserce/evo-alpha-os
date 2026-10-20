from data_job.core.direct_market_sources import (
    CNInfoDirectClient,
    EastmoneyDirectClient,
    parse_tencent_quote_payload,
)


def test_parse_tencent_quote_payload_maps_core_fields():
    parts = [""] * 50
    parts[1] = "测试股份"
    parts[2] = "600001"
    parts[3] = "12.34"
    parts[30] = "20260703150000"
    parts[32] = "3.21"
    parts[38] = "4.56"
    parts[39] = "22.5"
    parts[44] = "120"
    parts[45] = "180"
    parts[46] = "2.3"
    parts[47] = "13.57"
    parts[48] = "11.11"
    payload = f'v_sh600001="{"~".join(parts)}";'

    rows = parse_tencent_quote_payload(payload)

    assert rows[0]["code"] == "600001"
    assert rows[0]["name"] == "测试股份"
    assert rows[0]["trade_date"] == "2026-07-03"
    assert rows[0]["price"] == 12.34
    assert rows[0]["pe_ttm"] == 22.5
    assert rows[0]["total_mv"] == 18000000000
    assert rows[0]["limit_up_price"] == 13.57


def test_eastmoney_datacenter_returns_result_data(monkeypatch):
    client = EastmoneyDirectClient(min_interval=0)

    monkeypatch.setattr(
        client,
        "em_get",
        lambda _url, _params: {"result": {"data": [{"SECURITY_CODE": "600001"}]}},
    )

    rows = client.datacenter(report_name="RPT_TEST")

    assert rows == [{"SECURITY_CODE": "600001"}]


def test_eastmoney_datacenter_pages_stops_on_short_page(monkeypatch):
    client = EastmoneyDirectClient(min_interval=0)
    calls = []

    def fake_datacenter(**kwargs):
        calls.append(kwargs["page_number"])
        if kwargs["page_number"] == 1:
            return [{"SECURITY_CODE": "600001"}]
        return []

    monkeypatch.setattr(client, "datacenter", fake_datacenter)

    rows = client.datacenter_pages(report_name="RPT_TEST", page_size=50, max_pages=3)

    assert rows == [{"SECURITY_CODE": "600001"}]
    assert calls == [1]


def test_cninfo_announcements_uses_dynamic_org_id(monkeypatch):
    client = CNInfoDirectClient()
    posted = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"announcements": [{"announcementId": "a1", "announcementTitle": "公告"}]}

    monkeypatch.setattr(client, "org_map", lambda: {"688766": {"orgId": "gfbj0837540"}})

    def fake_post(_url, data, timeout):
        posted.update(data)
        return Response()

    monkeypatch.setattr(client.session, "post", fake_post)

    rows = client.announcements("688766")

    assert rows[0]["announcementId"] == "a1"
    assert posted["stock"] == "688766,gfbj0837540"

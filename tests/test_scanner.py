import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from scripts.scan_market import (
    is_eligible,
    market_rows,
    parse_sina_quotes,
    parse_tencent_quotes,
    symbol,
    technical_metrics,
    validate_feed,
)


class ScannerTests(unittest.TestCase):
    def test_market_symbols_include_beijing(self):
        self.assertEqual("sh600519", symbol("600519"))
        self.assertEqual("sz300750", symbol("300750"))
        self.assertEqual("bj920001", symbol("920001"))
        self.assertEqual("bj830001", symbol("830001"))

    def test_filters_st_and_new_listings(self):
        now = dt.datetime(2026, 7, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
        base = {
            "f14": "正常股份", "f2": 10, "f3": 2, "f5": 1000, "f6": 80_000_000,
            "f8": 3, "f10": 1.2, "f21": 3_000_000_000, "f26": 20200101,
        }
        self.assertTrue(is_eligible(base, now))
        self.assertFalse(is_eligible({**base, "f14": "ST风险"}, now))
        self.assertFalse(is_eligible({**base, "f26": 20260701}, now))

    def test_technical_metrics(self):
        rows = [
            {"date": f"2026-06-{index + 1:02d}", "close": 10 + index * 0.1, "volume": 1000 + index * 10}
            for index in range(30)
        ]
        result = technical_metrics(rows)
        self.assertTrue(result["above_ma20"])
        self.assertGreater(result["ma5"], result["ma20"])

    def test_quote_parsers(self):
        fields = [""] * 33
        fields[1], fields[2], fields[3], fields[30], fields[32] = "贵州茅台", "600519", "1214.88", "20260715150000", "0.32"
        tencent = parse_tencent_quotes(f'v_sh600519="{"~".join(fields)}";')
        self.assertAlmostEqual(1214.88, tencent["600519"]["price"])

        sina_fields = ["0"] * 32
        sina_fields[0], sina_fields[2], sina_fields[3] = "贵州茅台", "1210.99", "1214.88"
        sina_fields[30], sina_fields[31] = "2026-07-15", "15:00:00"
        sina = parse_sina_quotes(f'var hq_str_sh600519="{",".join(sina_fields)}";')
        self.assertAlmostEqual(1214.88, sina["600519"]["price"])

    def test_market_rows_supports_list_and_mapping_shapes(self):
        self.assertEqual([{"f12": "600001"}], market_rows({"diff": [{"f12": "600001"}]}))
        self.assertEqual([{"f12": "600002"}], market_rows({"diff": {"0": {"f12": "600002"}}}))

    def test_feed_validation_rejects_partial_market(self):
        with self.assertRaises(ValueError):
            validate_feed({"schema_version": 1, "coverage": {"market_count": 20}, "candidates": []})


if __name__ == "__main__":
    unittest.main()

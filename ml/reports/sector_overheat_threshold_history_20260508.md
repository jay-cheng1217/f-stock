# High-Risk Sector OVERHEAT Threshold Historical Audit

- Generated at: `2026-05-08T12:05:22.324684+00:00`
- Window: `2024-01-01` to `2026-05-08`
- High-risk tickers scanned: `149`
- Full 20D event rows: `80025`
- Canonical event return: `Open[t+1] -> Close[t+20]`

## Threshold Summary

| threshold | triggers | tickers | days | avg 20D | median 20D | positive | avg alpha | MDD |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| +20% | 2618 | 102 | 499 | -0.72% | -4.70% | +38.46% | -4.16% | -61.69% |
| +25% | 1764 | 77 | 460 | -0.32% | -4.85% | +39.91% | -4.08% | -66.91% |
| +30% | 1268 | 62 | 418 | -0.25% | -4.70% | +40.22% | -4.46% | -58.54% |

## By Group

| threshold | group | triggers | tickers | avg 20D | positive | avg alpha |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| +20% | 塑化 / 石化 | 1549 | 53 | +1.81% | +43.25% | -2.21% |
| +20% | 航運 | 585 | 24 | -3.34% | +31.79% | -6.93% |
| +20% | 鋼鐵 / 金屬 | 484 | 25 | -5.62% | +31.20% | -7.04% |
| +25% | 塑化 / 石化 | 1066 | 44 | +2.56% | +45.59% | -1.81% |
| +25% | 航運 | 386 | 19 | -3.58% | +32.38% | -8.20% |
| +25% | 鋼鐵 / 金屬 | 312 | 14 | -6.12% | +29.81% | -6.75% |
| +30% | 塑化 / 石化 | 785 | 35 | +3.10% | +47.01% | -1.63% |
| +30% | 航運 | 285 | 15 | -5.35% | +29.82% | -10.40% |
| +30% | 鋼鐵 / 金屬 | 198 | 12 | -6.22% | +28.28% | -7.14% |

## Notes

- This is an event study over high-risk sectors, not a production signal replay.
- It answers whether lower sector thresholds historically captured bad forward returns in these sectors.
- MDD is computed on monthly averaged event baskets to avoid double-counting heavily overlapping 20D event windows.
- Production promotion still needs same-snapshot signal-level A/B if this gate changes live selection.

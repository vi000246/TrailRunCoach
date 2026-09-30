"""Race-power calculator — a port of the SuperPower Calculator workbook plus
trail / 百岳 extensions (docs/research/superpower-calculator.md).

    env      environment multiplier M (altitude + heat)
    riegel   Riegel tasks 7–10 / 12, lookup table, personal ln-ln fit
    cp       CP / W′ from efforts, validity checks, RWC rating
    re       running effectiveness, CVI adjustment, trail RE, activity metrics
    predict  road / trail / 百岳 predictions and scenarios
    weather  CWA / Open-Meteo providers, cache, key storage, peak lookup
    athlete  reads the Dataset to fill the athlete's defaults
"""

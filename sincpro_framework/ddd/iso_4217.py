"""ISO 4217: every currency in circulation, by its code, and how many decimals its amounts carry.

    ISO_4217["BOB"]  ->  2        ISO_4217["JPY"]  ->  0        ISO_4217["KWD"]  ->  3

Context: the list the standard publishes (its "minor unit"), without the metals, the testing
codes and the codes withdrawn. A currency it does not hold — a crypto, points, a bank's unit of
account — is a `Currency` given its decimals, never an entry added here.
"""

from collections.abc import Mapping

NO_DECIMALS = frozenset(
    {
        "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF",
        "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
    }
)  # fmt: skip
"""The currencies whose amounts have no decimals."""

THREE_DECIMALS = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})
"""The currencies whose amounts carry three decimals."""

FOUR_DECIMALS = frozenset({"CLF", "UYW"})
"""The units of account whose amounts carry four decimals."""

CODES = (
    "AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD BTN BWP "
    "BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB "
    "EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD "
    "JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP "
    "MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR "
    "RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND "
    "TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAF XCD XCG XOF XPF "
    "YER ZAR ZMW ZWG"
).split()
"""Every code in circulation."""

ISO_4217: Mapping[str, int] = {
    code: (
        0
        if code in NO_DECIMALS
        else 3 if code in THREE_DECIMALS else 4 if code in FOUR_DECIMALS else 2
    )
    for code in CODES
}
"""Each code and the decimals its amounts carry."""

"""Product radar scope. Focus markets govern scheduled coverage, not admissible discoveries."""
import json
from datetime import date
from pathlib import Path


DEFAULT_MARKETS = ("MY", "ID", "PH", "GB", "TH", "CA")
CONFIG_FILENAME = "radar-markets.json"
# ISO 3166-1 alpha-2; English display names from IANA tzdata's public-domain
# iso3166.tab (2025), keeping the existing UK label. Embedded for offline runs.
_MARKETS = """
AD Andorra;AE United Arab Emirates;AF Afghanistan;AG Antigua & Barbuda
AI Anguilla;AL Albania;AM Armenia;AO Angola;AQ Antarctica;AR Argentina
AS Samoa (American);AT Austria;AU Australia;AW Aruba;AX Åland Islands;AZ Azerbaijan
BA Bosnia & Herzegovina;BB Barbados;BD Bangladesh;BE Belgium;BF Burkina Faso
BG Bulgaria;BH Bahrain;BI Burundi;BJ Benin;BL St Barthelemy;BM Bermuda
BN Brunei;BO Bolivia;BQ Caribbean NL;BR Brazil;BS Bahamas;BT Bhutan;BV Bouvet Island
BW Botswana;BY Belarus;BZ Belize;CA Canada;CC Cocos (Keeling) Islands;CD Congo (Dem. Rep.)
CF Central African Rep.;CG Congo (Rep.);CH Switzerland;CI Côte d’Ivoire
CK Cook Islands;CL Chile;CM Cameroon;CN China;CO Colombia;CR Costa Rica
CU Cuba;CV Cape Verde;CW Curaçao;CX Christmas Island;CY Cyprus;CZ Czech Republic
DE Germany;DJ Djibouti;DK Denmark;DM Dominica;DO Dominican Republic;DZ Algeria
EC Ecuador;EE Estonia;EG Egypt;EH Western Sahara;ER Eritrea;ES Spain;ET Ethiopia
FI Finland;FJ Fiji;FK Falkland Islands;FM Micronesia;FO Faroe Islands;FR France
GA Gabon;GB United Kingdom;GD Grenada;GE Georgia;GF French Guiana;GG Guernsey
GH Ghana;GI Gibraltar;GL Greenland;GM Gambia;GN Guinea;GP Guadeloupe;GQ Equatorial Guinea
GR Greece;GS South Georgia & the South Sandwich Islands;GT Guatemala;GU Guam
GW Guinea-Bissau;GY Guyana;HK Hong Kong;HM Heard Island & McDonald Islands
HN Honduras;HR Croatia;HT Haiti;HU Hungary;ID Indonesia;IE Ireland;IL Israel
IM Isle of Man;IN India;IO British Indian Ocean Territory;IQ Iraq;IR Iran
IS Iceland;IT Italy;JE Jersey;JM Jamaica;JO Jordan;JP Japan;KE Kenya;KG Kyrgyzstan
KH Cambodia;KI Kiribati;KM Comoros;KN St Kitts & Nevis;KP Korea (North)
KR Korea (South);KW Kuwait;KY Cayman Islands;KZ Kazakhstan;LA Laos;LB Lebanon
LC St Lucia;LI Liechtenstein;LK Sri Lanka;LR Liberia;LS Lesotho;LT Lithuania
LU Luxembourg;LV Latvia;LY Libya;MA Morocco;MC Monaco;MD Moldova;ME Montenegro
MF St Martin (French);MG Madagascar;MH Marshall Islands;MK North Macedonia
ML Mali;MM Myanmar (Burma);MN Mongolia;MO Macau;MP Northern Mariana Islands
MQ Martinique;MR Mauritania;MS Montserrat;MT Malta;MU Mauritius;MV Maldives
MW Malawi;MX Mexico;MY Malaysia;MZ Mozambique;NA Namibia;NC New Caledonia
NE Niger;NF Norfolk Island;NG Nigeria;NI Nicaragua;NL Netherlands;NO Norway
NP Nepal;NR Nauru;NU Niue;NZ New Zealand;OM Oman;PA Panama;PE Peru;PF French Polynesia
PG Papua New Guinea;PH Philippines;PK Pakistan;PL Poland;PM St Pierre & Miquelon
PN Pitcairn;PR Puerto Rico;PS Palestine;PT Portugal;PW Palau;PY Paraguay
QA Qatar;RE Réunion;RO Romania;RS Serbia;RU Russia;RW Rwanda;SA Saudi Arabia
SB Solomon Islands;SC Seychelles;SD Sudan;SE Sweden;SG Singapore;SH St Helena
SI Slovenia;SJ Svalbard & Jan Mayen;SK Slovakia;SL Sierra Leone;SM San Marino
SN Senegal;SO Somalia;SR Suriname;SS South Sudan;ST Sao Tome & Principe
SV El Salvador;SX St Maarten (Dutch);SY Syria;SZ Eswatini (Swaziland);TC Turks & Caicos Is
TD Chad;TF French S. Terr.;TG Togo;TH Thailand;TJ Tajikistan;TK Tokelau
TL East Timor;TM Turkmenistan;TN Tunisia;TO Tonga;TR Turkey;TT Trinidad & Tobago
TV Tuvalu;TW Taiwan;TZ Tanzania;UA Ukraine;UG Uganda;UM US minor outlying islands
US United States;UY Uruguay;UZ Uzbekistan;VA Vatican City;VC St Vincent
VE Venezuela;VG Virgin Islands (UK);VI Virgin Islands (US);VN Vietnam;VU Vanuatu
WF Wallis & Futuna;WS Samoa (western);YE Yemen;YT Mayotte;ZA South Africa
ZM Zambia;ZW Zimbabwe
"""
_NAMES = dict(entry.split(" ", 1) for line in _MARKETS.strip().splitlines() for entry in line.split(";"))


def is_market_code(code) -> bool:
    return isinstance(code, str) and code in _NAMES


def market_name(code: str) -> str:
    return _NAMES[code]


def validate_markets(values) -> tuple:
    """Require a non-empty ordered list/tuple of distinct canonical ISO codes."""
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("focus_markets must be a non-empty array of ISO 3166-1 alpha-2 codes")
    for code in values:
        if not is_market_code(code):
            raise ValueError(f"invalid market code {code!r}: expected an assigned uppercase ISO alpha-2 code")
    if len(set(values)) != len(values):
        raise ValueError("duplicate market code in focus_markets")
    return tuple(values)


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot read {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def configured_markets(work_dir: Path) -> tuple:
    path = Path(work_dir) / CONFIG_FILENAME
    if not path.exists():
        return DEFAULT_MARKETS
    config = _read_object(path)
    try:
        return validate_markets(config.get("focus_markets"))
    except ValueError as error:
        raise ValueError(f"{path.name}: {error}") from error


def expected_run_markets(work_dir: Path, report_date: str) -> tuple:
    """Use the immutable-for-this-run plan first; support pre-snapshot manifests."""
    if date.fromisoformat(report_date).isoformat() != report_date:
        raise ValueError("report_date must be YYYY-MM-DD")
    path = Path(work_dir) / f"product-radar-input-{report_date}.json"
    if not path.exists():
        return configured_markets(work_dir)
    manifest = _read_object(path)
    if manifest.get("report_date") != report_date:
        raise ValueError(f"{path.name}: report_date does not match {report_date}")
    countries = manifest.get("countries")
    if not isinstance(countries, list) or not all(isinstance(item, dict) for item in countries):
        raise ValueError(f"{path.name}: countries must be an array of market objects")
    try:
        planned = validate_markets([item.get("code") for item in countries])
        if "focus_markets" in manifest and validate_markets(manifest["focus_markets"]) != planned:
            raise ValueError("focus_markets differs from countries")
    except ValueError as error:
        raise ValueError(f"{path.name}: {error}") from error
    return planned

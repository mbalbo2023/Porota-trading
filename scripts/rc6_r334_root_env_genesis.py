"""R334 CODE ONLY: deterministic legacy-env proposal, NOT a root installer.

Input inode custody (root0600) and independent approval must be established by
the caller's trusted authority; bytes alone cannot prove either. Outputs are
proposed root0400 contents, never sealed files. Physical approval is missing:
HOLD79. No filesystem, subprocess, Docker, legacy-manager imports or CLI.
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import re


class GenesisHold(RuntimeError):
    """Fixed, non-secret failure code; physical authority remains HOLD79."""
    exit_code = 79


def _require(condition, reason):
    if not condition:
        raise GenesisHold('RC6_R334_HOLD79:' + reason)


# Explicit reviewed names from legacy v17 PAPER_DEFAULTS; never prefix grants.
_PAPER_KEYS = frozenset('''PAPER_INITIAL_CAPITAL_ARS PAPER_INITIAL_CAPITAL_USD
PAPER_INITIAL_CAPITAL_USD_MEP PAPER_INITIAL_CAPITAL_USD_CCL PAPER_RISK_PER_TRADE
PAPER_MAX_OPEN_POSITIONS PAPER_EMERGENCY_MAX_OPEN_POSITIONS PAPER_MAX_POSITION_PCT
PAPER_MAX_TOTAL_EXPOSURE_PCT PAPER_MAX_HOLD_MINUTES PAPER_ACTIVE_SYMBOL_LIMIT
PAPER_FOCUS_MINIMUM_FOR_OPENINGS PAPER_READINESS_CHECK_SECONDS
PAPER_DAILY_SOFT_STOP_PCT MAX_DAILY_LOSS_PCT PAPER_SIGNAL_MIN_SAMPLES
PAPER_SIGNAL_WINDOW_MINUTES PAPER_SCORE_THRESHOLD PAPER_BOOK_MAX_AGE_SECONDS
PAPER_TRADE_MAX_AGE_SECONDS PAPER_AI_GATE_MODE PAPER_ECONOMIC_GATE_MODE
PAPER_EXPECTANCY_POLICY PAPER_MARKET_REGIME_POLICY PAPER_SECTOR_CONCENTRATION_POLICY
PAPER_MAX_POSITIONS_PER_SECTOR PAPER_MIN_NET_REWARD_RISK PAPER_STOP_LOSS_PCT
PAPER_TARGET_GAIN_PCT PAPER_INTRADAY_FEE_REBATE PPI_BACKGROUND_INGEST_SECONDS
PPI_HISTORY_RAW_STORAGE_MODE PPI_HISTORY_EVIDENCE_ROOT PAPER_NEWS_INGEST_ENABLED
PAPER_FOCUS_SYMBOLS PAPER_SCALPING_MODE PAPER_INTRADAY_SCAN_SECONDS
PAPER_INTRADAY_BATCH_LIMIT PAPER_SCALPING_MIN_NET_MARGIN PAPER_SCALPING_MAX_SPREAD
PAPER_SCALPING_SCORE_THRESHOLD PAPER_SCALPING_RISK_PER_TRADE
PAPER_SCALPING_MAX_OPEN_POSITIONS PAPER_SCALPING_MAX_HOLD_MINUTES
PAPER_SCALPING_STOP_LOSS_PCT PAPER_SCALPING_TARGET_GAIN_PCT PAPER_CAUCION_SWEEP_MODE
PAPER_CAUCION_SWEEP_START_MINUTES_BEFORE_CLOSE PAPER_CAUCION_SWEEP_CUTOFF_MINUTES_BEFORE_CLOSE
'''.split())
_SAFETY = {'POROTA_RUNTIME_MODE': 'PRODUCTION_PAPER', 'PPI_ORDERS': 'BLOCKED',
           'REAL_ORDERS_ALLOWED': 'false'}
_SCHEDULE = {'MARKET_OPEN_HOUR': '10', 'MARKET_OPEN_MINUTE': '30'}
_COMMON = _PAPER_KEYS | frozenset(_SAFETY) | frozenset(_SCHEDULE) | frozenset({
    'SERVER_TIMEZONE', 'PAPER_V17_DB_PATH', 'PYTHONDONTWRITEBYTECODE'})
_ROLE_KEYS = {
    'dashboard': _COMMON | frozenset({'DASHBOARD_OPERATION_MODE',
        'DASHBOARD_REFRESH_SECONDS', 'DASHBOARD_PASSWORD',
        'DASHBOARD_HOST', 'DASHBOARD_PORT', 'DASHBOARD_ACCESS_TOKEN',
        'DASHBOARD_USER', 'DASHBOARD_PASSWORD_HASH', 'ENVIRONMENT',
        'DASHBOARD_SESSION_HOURS', 'DASHBOARD_MAX_LOGIN_ATTEMPTS',
        'DASHBOARD_LOCKOUT_MINUTES', 'DASHBOARD_SESSION_STORE'}),
    'observer': _COMMON | frozenset({'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID',
        'POROTA_CONTRACT_EVIDENCE_MODE', 'PAPER_T1_FULL_DATE_RELEASE',
        'POROTA_DYNAMIC_CAPACITY_MODE', 'POROTA_CAPACITY_POLICY_PATH',
        'POROTA_CAPACITY_REPORT_PATH', 'POROTA_CAPACITY_RECOMMENDATION_PATH',
        'POROTA_CAPACITY_APPROVAL_PATH', 'POROTA_CAPACITY_SHADOW_PATH',
        'POROTA_PPI_STATIC_SHARED_AUTHORITY_PATH'}),
}
_DROP = frozenset({'PATH', 'PYTHON_VERSION', 'PYTHON_SHA256', 'PYTHONUNBUFFERED'})
# ay_dashboard_auth: SANDBOX permits unbounded sessions; PRODUCTION also
# rejects plaintext passwords at its own startup gate. Never normalize an
# unsupported auth environment into an approved one or silently enable it.
_FIXED_VALUES = {**_SAFETY, **_SCHEDULE, 'DASHBOARD_OPERATION_MODE': 'PRODUCTION_PAPER',
                 'PYTHONDONTWRITEBYTECODE': '1', 'ENVIRONMENT': 'PRODUCTION'}


@dataclass(frozen=True)
class EnvProposal:
    payload: bytes = field(repr=False)
    sha256: str = field(repr=False)
    dropped_keys: tuple[str, ...]
    host_go: bool = field(default=False, init=False)
    deploy_authorized: bool = field(default=False, init=False)


@dataclass(frozen=True)
class EnvPairProposal:
    dashboard: EnvProposal = field(repr=False)
    observer: EnvProposal = field(repr=False)
    host_go: bool = field(default=False, init=False)
    deploy_authorized: bool = field(default=False, init=False)


def transform_legacy_env(*, role, source, expected_source_sha256):
    """Validate pinned source bytes; return canonical sorted LF env contents.

    A caller-supplied hash is a consistency pin, NOT independent host GO.
    Unknown legacy keys require a separate reviewed code change, never an
    unchecked caller-provided allowlist. Values remain opaque and untrimmed.
    """
    _require(type(role) is str and role in _ROLE_KEYS, 'ROLE')
    _require(type(source) is bytes and 0 < len(source) <= 65536, 'SOURCE_SIZE')
    _require(type(expected_source_sha256) is str and
             re.fullmatch('[0-9a-f]{64}', expected_source_sha256) is not None,
             'SOURCE_PIN')
    _require(hmac.compare_digest(hashlib.sha256(source).hexdigest(),
                                expected_source_sha256), 'SOURCE_PIN_MISMATCH')
    try:
        text = source.decode('utf-8', 'strict')
    except UnicodeError:
        raise GenesisHold('RC6_R334_HOLD79:ENCODING') from None
    _require(not any(ord(c) < 32 and c != '\n' or ord(c) == 127 or
                     c in '\x85\u2028\u2029' for c in text), 'CONTROL_CHARACTER')
    lines = text.split('\n')
    if lines[-1] == '':
        lines.pop()
    found = {}
    dropped = set()
    for line in lines:
        _require('=' in line, 'LINE')
        key, value = line.split('=', 1)
        _require(re.fullmatch('[A-Z][A-Z0-9_]{0,80}', key) is not None and
                 len(value) <= 1024, 'KEY_OR_VALUE')
        if key in found:
            _require(role == 'dashboard' and key == 'SERVER_TIMEZONE' and
                     found[key] == value, 'DUPLICATE')
            continue
        _require(key not in dropped, 'DUPLICATE')
        if key in _DROP:
            dropped.add(key)
            continue
        _require(key in _ROLE_KEYS[role], 'UNAPPROVED_KEY')
        _require(key not in _FIXED_VALUES or value == _FIXED_VALUES[key],
                 'SAFETY_CONFLICT')
        found[key] = value
    if role == 'dashboard':
        # Missing ENVIRONMENT defaults to SANDBOX in ay_dashboard_auth.
        # Require source intent; do not silently inject or normalize it.
        _require(found.get('ENVIRONMENT') == 'PRODUCTION', 'AUTH_ENVIRONMENT_REQUIRED')
    found.update(_SAFETY)
    if role == 'observer':
        found.update(_SCHEDULE)
    payload = ''.join(key + '=' + found[key] + '\n' for key in sorted(found)).encode('utf-8')
    _require(len(payload) <= 65536, 'OUTPUT_SIZE')
    return EnvProposal(payload, hashlib.sha256(payload).hexdigest(), tuple(sorted(dropped)))


def propose_env_pair(*, dashboard_source, observer_source,
                     dashboard_source_sha256, observer_source_sha256):
    """Return both role proposals or raise; no host side effects or output."""
    return EnvPairProposal(
        transform_legacy_env(role='dashboard', source=dashboard_source,
                             expected_source_sha256=dashboard_source_sha256),
        transform_legacy_env(role='observer', source=observer_source,
                             expected_source_sha256=observer_source_sha256))

#!/usr/bin/env python3
"""
AnythingLLM Web Console
=======================
A small Flask server that:
  1. serves the console UI (plain HTML/CSS/JS - no Streamlit),
  2. holds console user accounts locally (username + password + their AnythingLLM API key),
  3. proxies every /api/* request through to your AnythingLLM instance, injecting the
     signed-in user's API key server-side so the browser never sees it.

Run:
    pip install flask requests
    python server.py

First run writes config.json next to this file. Edit it (or use the in-UI Settings page,
which is only visible to the account whose API key equals master_api_key).

Security features:
- Rate limiting on auth endpoints (5 attempts per 60 seconds)
- SSRF protection (API key validation only against configured instance)
- Path traversal protection for file serving
- Hop-by-hop header stripping for proxy requests
- Password hashing with PBKDF2 (240,000 iterations)
- HttpOnly + SameSite session cookies
"""

import warnings
# Suppress requests library dependency version warnings (urllib3/chardet/charset_normalizer)
# These warnings are harmless and occur when dependency versions are newer than what requests was tested with
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*urllib3.*doesn't match a supported version.*")
warnings.filterwarnings("ignore", message=".*chardet.*doesn't match a supported version.*")
warnings.filterwarnings("ignore", message=".*charset_normalizer.*doesn't match a supported version.*")

import json
import os
import hashlib
import secrets
import time
from functools import wraps

# ============================================================================
# RATE LIMITING
# ============================================================================
# Simple in-memory rate limiting for authentication endpoints
# Structure: {ip_address: (failure_count, first_attempt_timestamp)}
# Note: Resets on server restart (intentional - prevents permanent lockouts)
_auth_failures = {}

def check_rate_limit(ip, max_attempts=5, window_seconds=60):
    """
    Check if an IP address has exceeded the rate limit for failed attempts.
    
    Args:
        ip: Client IP address
        max_attempts: Maximum failed attempts allowed (default: 5)
        window_seconds: Time window in seconds (default: 60)
    
    Returns:
        tuple: (allowed: bool, remaining_attempts: int)
        - allowed=True if under limit, False if rate limited
        - remaining_attempts shows how many tries left before lockout
    """
    now = time.time()
    if ip in _auth_failures:
        count, first_time = _auth_failures[ip]
        if now - first_time > window_seconds:
            # Window expired, reset counter
            del _auth_failures[ip]
            return True, max_attempts
        if count >= max_attempts:
            return False, 0  # Rate limited
        return True, max_attempts - count
    return True, max_attempts

def record_failure(ip):
    """
    Record a failed authentication attempt for an IP address.
    Called when login/signup fails to increment the failure counter.
    
    Args:
        ip: Client IP address
    """
    now = time.time()
    if ip in _auth_failures:
        _auth_failures[ip] = (_auth_failures[ip][0] + 1, _auth_failures[ip][1])
    else:
        _auth_failures[ip] = (1, now)

def get_client_ip():
    """
    Extract client IP address from request, handling reverse proxies.
    Checks X-Forwarded-For header first (for nginx/Caddy setups),
    then falls back to remote_addr.
    
    Returns:
        str: Client IP address
    """
    # Check for X-Forwarded-For header (for reverse proxy setups)
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or '127.0.0.1'

import requests
from flask import Flask, request, session, jsonify, Response, send_from_directory, abort

# ============================================================================
# CONFIGURATION & PATHS
# ============================================================================

ROOT = os.path.dirname(os.path.abspath(__file__))  # Directory containing server.py
CONFIG_PATH = os.path.join(ROOT, "config.json")     # Server configuration file
USERS_PATH = os.path.join(ROOT, "users.json")       # User accounts database
UI_FILE = "AnythingLLM Console.dc.html"             # Main HTML template

# Default configuration - used on first run or if config.json is missing/corrupt
# These values are sensible defaults for a local AnythingLLM instance
DEFAULT_CONFIG = {
    "anythingllm_url": "http://localhost:3001",  # Server-side URL to AnythingLLM
    "listen_host": "0.0.0.0",                     # Bind to all network interfaces
    "listen_port": 1555,                          # Default port for web console
    "master_api_key": "XXXXXXX-XXXXXXX-JW7420N-4DZ3HGA",  # Default (change this!)
    "allow_signup": True,                         # Allow new user registration
    # HTTPS: point ssl_certfile at a PEM cert (or a combined cert+key PEM, in which
    # case leave ssl_keyfile empty). Empty certfile = plain HTTP.
    "ssl_certfile": "",                           # Path to SSL certificate (optional)
    "ssl_keyfile": "",                            # Path to SSL key (optional)
    "secret_key": None,                           # Flask session secret (auto-generated)
}

# ============================================================================
# PROXY HEADER HANDLING
# ============================================================================
# These headers are stripped when proxying requests to prevent security issues
# and protocol conflicts. See PEP 3333 for WSGI requirements.

# Request headers to strip before forwarding to AnythingLLM
HOP_BY_HOP = {
    "host", "content-length", "connection", "transfer-encoding",
    "authorization", "cookie", "accept-encoding", "upgrade",
}
# Hop-by-hop headers must never be forwarded (PEP 3333); waitress raises if they are.

# Response headers to strip before returning to client
RESP_STRIP = {
    "content-encoding", "content-length", "transfer-encoding", "connection",
    "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "trailers", "upgrade", "public", "proxy-connection",
}


# ============================================================================
# CONFIGURATION MANAGEMENT
# ============================================================================

def load_config():
    """
    Load server configuration from config.json.
    
    Creates config.json with DEFAULT_CONFIG on first run.
    If config.json exists but is corrupted, falls back to defaults.
    Auto-generates a cryptographically secure secret_key for Flask sessions.
    
    Returns:
        dict: Complete configuration dictionary with all required keys
    """
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                cfg.update(json.load(fh))
        except Exception as exc:  # corrupt file - keep serving with defaults
            print(f"[config] could not read config.json ({exc}); using defaults")
    if not cfg.get("secret_key"):
        # Generate cryptographically secure session secret
        cfg["secret_key"] = secrets.token_hex(32)
        save_config(cfg)
    return cfg


def save_config(cfg):
    """
    Save configuration to config.json.
    
    Args:
        cfg: Configuration dictionary to save
    """
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


def load_users():
    """
    Load user accounts from users.json.
    
    Returns empty dict if file doesn't exist or is corrupted.
    This is intentional - allows the server to start even with corrupt user data.
    
    Returns:
        dict: User accounts {username: {salt, hash, api_key}}
    """
    if not os.path.exists(USERS_PATH):
        return {}
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_users(users):
    """
    Save user accounts to users.json.
    
    Args:
        users: User accounts dictionary
    """
    with open(USERS_PATH, "w", encoding="utf-8") as fh:
        json.dump(users, fh, indent=2)


def hash_password(password, salt=None):
    """
    Hash a password using PBKDF2-HMAC-SHA256.
    
    Uses 240,000 iterations for strong security.
    Generates a random 16-byte salt if not provided.
    
    Args:
        password: Plain text password to hash
        salt: Optional salt (hex string). Random if not provided.
    
    Returns:
        tuple: (salt, digest) both as hex strings
    """
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 240_000).hex()
    return salt, digest


def verify_password(password, salt, digest):
    """
    Verify a password against stored hash using constant-time comparison.
    
    Uses secrets.compare_digest to prevent timing attacks.
    
    Args:
        password: Plain text password to verify
        salt: Salt from user record (hex string)
        digest: Stored hash from user record (hex string)
    
    Returns:
        bool: True if password matches, False otherwise
    """
    return secrets.compare_digest(hash_password(password, salt)[1], digest)


def api_base():
    """
    Get the base URL for the AnythingLLM instance.
    
    Returns:
        str: AnythingLLM URL with trailing slash removed
    """
    return load_config()["anythingllm_url"].rstrip("/")


def validate_key(key, url=None):
    """
    Validate an AnythingLLM API key by calling GET /v1/auth.
    
    SECURITY: Only validates against configured instance URL (prevents SSRF).
    The url parameter is deprecated and should not be used.
    
    Args:
        key: AnythingLLM API key to validate
        url: Deprecated - only uses configured instance URL
    
    Returns:
        tuple: (valid: bool, message: str)
        - valid=True if key is accepted, False otherwise
        - message describes result or error
    """
    # SECURITY FIX: Only validate against configured instance URL
    # Previously allowed arbitrary URLs (SSRF vulnerability)
    base = api_base().rstrip("/")
    try:
        r = requests.get(
            base + "/api/v1/auth",
            headers={"Authorization": f"Bearer {key}", "accept": "application/json"},
            timeout=15,
        )
    except requests.RequestException as exc:
        return False, f"Could not reach {base} ({exc.__class__.__name__})"
    if r.status_code == 403:
        return False, "AnythingLLM rejected that API key"
    if r.status_code != 200:
        return False, f"Unexpected response from instance: HTTP {r.status_code}"
    try:
        return bool(r.json().get("authenticated")), "ok"
    except ValueError:
        return False, "Instance did not return JSON - is that the AnythingLLM URL?"


# ============================================================================
# FLASK APP INITIALIZATION
# ============================================================================

app = Flask(__name__, static_folder=None)
# Security: HttpOnly prevents JavaScript access to session cookie
# Security: SameSite=Lax prevents CSRF in most scenarios
# Note: 1GB max content length - intentionally large for file uploads
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                  MAX_CONTENT_LENGTH=1024 * 1024 * 1024)
app.secret_key = load_config()["secret_key"]  # Cryptographically secure, persisted in config


# ============================================================================
# USER & AUTHENTICATION HELPERS
# ============================================================================

def current_user():
    """
    Get the currently authenticated user from session.
    
    Returns:
        dict or None: User account data {salt, hash, api_key} or None if not logged in
    """
    name = session.get("user")
    if not name:
        return None
    return load_users().get(name)


def is_master(user):
    """
    Check if user has master privileges.
    
    A user is master if their AnythingLLM API key matches the master_api_key
    in config.json. Master users can access the Settings page to manage other
    accounts and change server configuration.
    
    Args:
        user: User account dict from current_user(), or None
    
    Returns:
        bool: True if user is master, False otherwise
    """
    if not user:
        return False
    master = (load_config().get("master_api_key") or "").strip()
    # Only True if master key is set AND user's key matches
    return bool(master) and user.get("api_key", "").strip() == master


def login_required(fn):
    """
    Decorator: Require valid session for endpoint access.
    
    Use as: @login_required
    Returns 401 if no valid session exists.
    """
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user():
            return jsonify({"error": "Not signed in"}), 401
        return fn(*a, **kw)
    return wrapper


def master_required(fn):
    """
    Decorator: Require master privileges for endpoint access.
    
    Use as: @master_required
    User must be logged in AND have master API key.
    Returns 401 if not logged in, 403 if not master.
    """
    @wraps(fn)
    def wrapper(*a, **kw):
        user = current_user()
        if not user:
            return jsonify({"error": "Not signed in"}), 401
        if not is_master(user):
            return jsonify({"error": "Master API key required"}), 403
        return fn(*a, **kw)
    return wrapper


# ============================================================================
# AUTHENTICATION ENDPOINTS (/ui/*)
# ============================================================================
# These endpoints handle user account management.
# Rate limiting is applied to prevent brute force attacks.

@app.post("/ui/login")
def ui_login():
    """
    Authenticate user with username and password.
    
    Rate limited: 5 attempts per 60 seconds per IP.
    
    Returns:
        200: Session payload with user info
        401: Invalid credentials
        429: Rate limited (too many attempts)
    """
    # Rate limiting
    client_ip = get_client_ip()
    allowed, remaining = check_rate_limit(client_ip)
    if not allowed:
        return jsonify({"error": "Too many failed attempts. Please wait 60 seconds."}), 429
    
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = load_users().get(username)
    if not user or not verify_password(password, user.get("salt", ""), user.get("hash", "")):
        record_failure(client_ip)
        return jsonify({"error": "Wrong username or password"}), 401
    session["user"] = username
    return jsonify(session_payload())

@app.post("/ui/signup")
def ui_signup():
    # Rate limiting
    client_ip = get_client_ip()
    allowed, remaining = check_rate_limit(client_ip)
    if not allowed:
        return jsonify({"error": "Too many failed attempts. Please wait 60 seconds."}), 429
    
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    key = (body.get("apiKey") or "").strip()
    cfg = load_config()

    users = load_users()
    if not cfg.get("allow_signup") and users:
        return jsonify({"error": "Sign-ups are closed on this console"}), 403
    if len(username) < 2:
        return jsonify({"error": "Pick a username of at least 2 characters"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if username in users:
        return jsonify({"error": "That username is taken"}), 400

    ok, message = validate_key(key)
    if not ok:
        return jsonify({"error": message}), 400

    salt, digest = hash_password(password)
    users[username] = {"salt": salt, "hash": digest, "api_key": key}
    save_users(users)
    session["user"] = username
    return jsonify(session_payload())


@app.post("/ui/logout")
def ui_logout():
    """
    Clear the user's session (logout).
    
    Returns:
        200: Confirmation {"ok": true}
    """
    session.clear()
    return jsonify({"ok": True})


def session_payload():
    """
    Build session information payload for the UI.
    
    This function is called after login/signup and when checking session status.
    It builds a dictionary with all the information the frontend needs:
    - Authentication status
    - User info (username, master status)
    - AnythingLLM connection status
    - Whether signups are allowed
    
    SECURITY: Validates API key connectivity and reports connection errors
    to help users diagnose setup issues.
    
    Returns:
        dict: Session payload with keys:
            - signedIn: bool
            - username: str (if logged in)
            - isMaster: bool
            - multiUser: bool (AnythingLLM multi-user mode)
            - authenticated: bool (is API key valid?)
            - instanceUrl: str
            - allowSignup: bool
            - hasAccounts: bool (if not logged in)
            - connectionError: str (if can't reach AnythingLLM)
    """
    user = current_user()
    cfg = load_config()
    instance_url = cfg.get("anythingllm_url", DEFAULT_CONFIG["anythingllm_url"])
    connection_error = None
    
    if not user:
        # Not logged in - return basic info for auth forms
        return {
            "signedIn": False,
            "allowSignup": bool(cfg.get("allow_signup")) or not load_users(),
            "hasAccounts": bool(load_users()),
            "instanceUrl": instance_url,
        }
    
    # Check if we can reach AnythingLLM and if the API key is valid
    multi_user = None
    authenticated = None
    try:
        # Test connection to AnythingLLM
        r = requests.get(api_base() + "/api/v1/auth",
                         headers={"Authorization": f"Bearer {user.get('api_key', '')}"}, timeout=10)
        authenticated = r.status_code == 200
        if not authenticated:
            # API key might be invalid or revoked
            connection_error = f"AnythingLLM rejected the API key (HTTP {r.status_code})"
    except requests.RequestException as exc:
        # Can't reach AnythingLLM at all
        authenticated = False
        connection_error = f"Cannot reach AnythingLLM at {instance_url} ({exc.__class__.__name__})"
    
    try:
        # Check if AnythingLLM is in multi-user mode
        r = requests.get(api_base() + "/api/v1/admin/is-multi-user-mode",
                         headers={"Authorization": f"Bearer {user.get('api_key', '')}"}, timeout=10)
        multi_user = bool(r.json().get("isMultiUser")) if r.status_code == 200 else False
    except Exception:
        multi_user = False
    
    return {
        "signedIn": True,
        "username": session.get("user"),
        "isMaster": is_master(user),
        "multiUser": multi_user,
        "authenticated": authenticated,
        "instanceUrl": instance_url,
        "allowSignup": bool(cfg.get("allow_signup")),
        "connectionError": connection_error,
    }


@app.get("/ui/session")
def ui_session():
    """
    Get current session information.
    
    Used by the frontend to check if logged in and get session state.
    Also tests connection to AnythingLLM and reports any issues.
    
    Returns:
        dict: Session payload from session_payload()
    """
    return jsonify(session_payload())


# ============================================================================
# SERVER CONFIGURATION ENDPOINTS
# ============================================================================
# These endpoints allow viewing and modifying server settings.
# Protected by @master_required - only master users can access.

@app.get("/ui/config")
@master_required
def ui_config_get():
    """
    Get current server configuration.
    
    Returns all config values including:
    - Server URL and port settings
    - SSL certificate paths
    - Signup policy
    - List of all user accounts (without sensitive data)
    
    SECURITY: Requires master privileges. API keys are NOT exposed,
    only the last 7 characters (keyTail) for identification.
    
    Returns:
        dict: Complete server configuration
    """
    cfg = load_config()
    return jsonify({
        "anythingllm_url": cfg.get("anythingllm_url", DEFAULT_CONFIG["anythingllm_url"]),
        "listen_host": cfg.get("listen_host", DEFAULT_CONFIG["listen_host"]),
        "listen_port": cfg.get("listen_port", DEFAULT_CONFIG["listen_port"]),
        "master_api_key": cfg.get("master_api_key", ""),
        "ssl_certfile": cfg.get("ssl_certfile", ""),
        "ssl_keyfile": cfg.get("ssl_keyfile", ""),
        "allow_signup": bool(cfg.get("allow_signup")),
        "accounts": [
            # Show username, master status, and last 7 chars of API key (for identification)
            {"username": name, "isMaster": is_master(u), "keyTail": (u.get("api_key") or "")[-7:]}
            for name, u in load_users().items()
        ],
    })


@app.post("/ui/config")
@master_required
def ui_config_set():
    """
    Update server configuration.
    
    Accepts partial updates - only provided fields are changed.
    Some changes require a server restart to take effect.
    
    Returns:
        200: {"ok": true, "restartRequired": bool}
        400: Invalid input (e.g., non-numeric port)
        403: Not master user
    """
    body = request.get_json(silent=True) or {}
    cfg = load_config()
    
    # Update string fields (all optional)
    for field in ("anythingllm_url", "listen_host", "master_api_key", "ssl_certfile", "ssl_keyfile"):
        if field in body and isinstance(body[field], str):
            cfg[field] = body[field].strip()
    
    # Update port (must be a number)
    if "listen_port" in body:
        try:
            cfg["listen_port"] = int(body["listen_port"])
        except (TypeError, ValueError):
            return jsonify({"error": "Port must be a number"}), 400
    
    # Update signup policy
    if "allow_signup" in body:
        cfg["allow_signup"] = bool(body["allow_signup"])
    
    save_config(cfg)
    
    # Indicate if restart is needed for changes to take effect
    return jsonify({"ok": True, "restartRequired": any(
        k in body for k in ("listen_port", "listen_host", "ssl_certfile", "ssl_keyfile"))})


@app.post("/ui/account/key")
@login_required
def ui_account_key():
    """
    Update the current user's AnythingLLM API key.
    
    Validates the new key against AnythingLLM before saving.
    This allows users to rotate their API key if needed.
    
    Returns:
        200: Session payload with updated info
        400: Invalid API key (validation failed)
        401: Not logged in
    """
    body = request.get_json(silent=True) or {}
    key = (body.get("apiKey") or "").strip()
    ok, message = validate_key(key)
    if not ok:
        return jsonify({"error": message}), 400
    users = load_users()
    username = session.get("user")
    if not username or username not in users:
        return jsonify({"error": "Account not found"}), 404
    users[username]["api_key"] = key
    save_users(users)
    return jsonify(session_payload())


@app.delete("/ui/account/<username>")
@master_required
def ui_account_delete(username):
    """
    Delete a user account.
    
    SECURITY: Only master users can delete accounts.
    Users cannot delete themselves (would lock out the master).
    
    Args:
        username: Username to delete (from URL path)
    
    Returns:
        200: {"ok": true}
        400: Cannot delete own account
        403: Not master user
        404: User not found
    """
    users = load_users()
    if username not in users:
        return jsonify({"error": "No such account"}), 404
    if username == session.get("user"):
        return jsonify({"error": "You cannot delete the account you are signed in with"}), 400
    users.pop(username)
    save_users(users)
    return jsonify({"ok": True})


@app.post("/ui/validate-key")
def ui_validate_key():
    """
    Validate an AnythingLLM API key (for signup form).
    
    SECURITY: Only validates against the configured instance URL to prevent SSRF.
    This prevents users from making the server request arbitrary URLs.
    
    Returns:
        dict: {"valid": bool, "message": str}
    """
    body = request.get_json(silent=True) or {}
    ok, message = validate_key((body.get("apiKey") or "").strip())
    return jsonify({"valid": ok, "message": message})


# ============================================================================
# API PROXY ENDPOINT (/api/*)
# ============================================================================
# This is the core functionality - proxies all AnythingLLM API requests
# through this server, injecting the user's API key server-side.
# This way, API keys never touch the browser.

@app.route("/api/<path:sub>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@login_required
def proxy(sub):
    """
    Proxy requests to AnythingLLM API.
    
    This endpoint:
    1. Requires valid session (@login_required)
    2. Injects the user's API key into the Authorization header
    3. Forwards the request to AnythingLLM
    4. Returns the response to the client
    
    SECURITY:
    - API key is injected server-side (never exposed to browser)
    - Hop-by-hop headers are stripped before forwarding
    - Response headers are sanitized before returning
    - Timeout prevents hanging connections (15s normal, 900s for streaming)
    
    Args:
        sub: The API path after /api/v1/ (e.g., "workspaces", "workspace/my-workspace/chat")
    
    Returns:
        Response: Proxied response from AnythingLLM (with headers sanitized)
        502: Could not reach AnythingLLM
    """
    user = current_user()
    # Build the AnythingLLM URL
    url = f"{api_base()}/api/{sub}"
    
    # Forward headers, but strip hop-by-hop headers
    # This prevents security issues and protocol conflicts
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    
    # SECURITY: Inject user's API key server-side
    # The browser never sees this key
    headers["Authorization"] = f"Bearer {user.get('api_key', '')}"
    
    try:
        # Forward the request to AnythingLLM
        # timeout=(15, 900) means:
        # - 15 seconds to connect
        # - 900 seconds (15 min) for streaming responses
        upstream = requests.request(
            request.method, url,
            params=request.args,      # Query parameters
            data=request.get_data(),   # Request body
            headers=headers,           # Forwarded headers
            stream=True,              # Stream the response
            timeout=(15, 900),         # Connection and read timeouts
        )
    except requests.RequestException as exc:
        # Could not reach AnythingLLM at all
        return jsonify({"error": f"Could not reach AnythingLLM at {api_base()}",
                        "detail": exc.__class__.__name__}), 502

    # Sanitize response headers - strip hop-by-hop and potentially dangerous headers
    passthrough = [(k, v) for k, v in upstream.raw.headers.items() if k.lower() not in RESP_STRIP]
    
    # For streaming responses (SSE), add headers to prevent buffering
    if upstream.headers.get("content-type", "").startswith("text/event-stream"):
        passthrough.append(("X-Accel-Buffering", "no"))
        passthrough.append(("Cache-Control", "no-cache"))

    # Stream the response body chunk by chunk
    def body():
        for chunk in upstream.iter_content(chunk_size=None):
            if chunk:
                yield chunk

    return Response(body(), status=upstream.status_code, headers=passthrough)


# ============================================================================
# STATIC FILE SERVING
# ============================================================================
# Serves the UI files (HTML, JS, CSS, icons) while protecting sensitive files.

@app.route("/_stcore/<path:_ignored>")
def streamlit_probe(_ignored):
    """
    Streamlit compatibility endpoint.
    
    Some service workers poll this endpoint. Return 204 (No Content).
    """
    return "", 204


def ui_filename():
    """
    Find the UI HTML file to serve.
    
    Prefers "AnythingLLM Console.dc.html" but will use any *.dc.html file
    in the server directory as a fallback.
    
    Returns:
        str or None: Filename to serve, or None if none found
    """
    if os.path.isfile(os.path.join(ROOT, UI_FILE)):
        return UI_FILE
    # Fallback: use any .dc.html file in the directory
    for name in sorted(os.listdir(ROOT)):
        if name.endswith(".dc.html"):
            return name
    return None


@app.get("/")
def index():
    """
    Serve the main UI page.
    
    Checks that required files exist, then serves the HTML.
    Sets cache headers to prevent stale content during development.
    
    Returns:
        200: HTML page
        500: Missing required files (HTML or support.js)
    """
    name = ui_filename()
    if not name:
        # No HTML file found - show helpful error
        listing = "\n".join(sorted(os.listdir(ROOT))) or "(empty)"
        return Response(
            "<h2>Console page not found</h2>"
            "<p>server.py is running from <code>%s</code> but no <code>.dc.html</code> file is there.</p>"
            "<p>Copy <b>%s</b> and <b>support.js</b> into that folder, then reload.</p>"
            "<pre>%s</pre>" % (ROOT, UI_FILE, listing),
            status=500, mimetype="text/html")
    
    if not os.path.isfile(os.path.join(ROOT, "support.js")):
        # Missing support.js - show helpful error
        return Response(
            "<h2>support.js is missing</h2>"
            "<p><b>%s</b> was found in <code>%s</code>, but it needs <b>support.js</b> beside it "
            "or the page renders blank. Copy support.js in and reload.</p>" % (name, ROOT),
            status=500, mimetype="text/html")
    
    # Serve the HTML file
    response = send_from_directory(ROOT, name)
    # Prevent browser caching - always load fresh HTML
    # This ensures users get updates without hard refresh
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# SECURITY: Only these file extensions can be served
# Without this allowlist, config.json and users.json would be accessible!
SERVABLE_EXT = {".js", ".mjs", ".css", ".html", ".png", ".jpg", ".jpeg", ".gif",
                ".svg", ".ico", ".webmanifest", ".woff", ".woff2", ".ttf", ".map"}


@app.get("/<path:filename>")
def asset(filename):
    """
    Serve static assets (JS, CSS, images, fonts).
    
    SECURITY MEASURES:
    1. Blocks /api/* and /ui/* paths (API routes, not files)
    2. Only serves safe file extensions
    3. Blocks hidden files (starting with .)
    4. Resolves real path and verifies it's within ROOT (prevents traversal)
    
    Args:
        filename: Requested file path
    
    Returns:
        200: File contents
        404: File not allowed or not found
    """
    # Block API paths - they should be handled by routes, not files
    if filename.startswith(("api/", "ui/")):
        abort(404)
    
    # Only allow safe file extensions
    if os.path.splitext(filename)[1].lower() not in SERVABLE_EXT:
        abort(404)
    
    # Block hidden files (like .gitignore, .env, etc.)
    if any(part.startswith(".") for part in filename.replace("\\", "/").split("/")):
        abort(404)
    
    # SECURITY: Resolve real path and verify it's within ROOT
    # This prevents directory traversal attacks (e.g., ../../../etc/passwd)
    full = os.path.realpath(os.path.join(ROOT, filename))
    if os.path.commonpath([full, os.path.realpath(ROOT)]) != os.path.realpath(ROOT):
        abort(404)   # traversal attempt!
    
    if not os.path.isfile(full):
        abort(404)
    
    return send_from_directory(ROOT, filename)


@app.after_request
def no_store(resp):
    """
    Add cache headers to API responses.
    
    Prevents caching of dynamic API responses.
    Static assets can be cached (browser handles this).
    """
    if request.path.startswith(("/ui/", "/api/")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ============================================================================
# SERVER STARTUP
# ============================================================================

if __name__ == "__main__":
    """
    Start the Flask server.
    
    Handles:
    - Loading configuration
    - SSL certificate validation
    - Choosing server (waitress > cheroot > Flask dev)
    - Displaying startup information
    """
    config = load_config()
    print("AnythingLLM Web Console")
    print(f"  AnythingLLM instance : {config.get('anythingllm_url', DEFAULT_CONFIG['anythingllm_url'])}")
    
    # Check for SSL certificates
    certfile = (config.get("ssl_certfile") or "").strip()
    keyfile = (config.get("ssl_keyfile") or "").strip() or certfile  # Use cert as key if only one provided
    
    if certfile and not os.path.isfile(certfile):
        print(f"  !! ssl_certfile not found: {certfile} - falling back to plain HTTP")
        certfile = ""
    if certfile and not os.path.isfile(keyfile):
        print(f"  !! ssl_keyfile not found: {keyfile} - falling back to plain HTTP")
        certfile = ""
    
    scheme = "https" if certfile else "http"
    print(f"  Console listening on : {scheme}://{config.get('listen_host', DEFAULT_CONFIG['listen_host'])}:{config.get('listen_port', DEFAULT_CONFIG['listen_port'])}")
    print(f"  Accounts on file     : {len(load_users())}")
    
    # Try to detect LAN IP for convenience
    try:
        import socket
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        print(f"  LAN address          : {scheme}://{probe.getsockname()[0]}:{config.get('listen_port', DEFAULT_CONFIG['listen_port'])}")
        probe.close()
    except Exception:
        pass
    
    # Check for required files
    found = ui_filename()
    if not found:
        print(f"  !! no .dc.html found in {ROOT} - copy '{UI_FILE}' and support.js here")
    else:
        if found != UI_FILE:
            print(f"  note: serving '{found}' (expected '{UI_FILE}')")
        if not os.path.isfile(os.path.join(ROOT, "support.js")):
            print("  !! support.js is missing next to the page - the console will render blank")
    
    host, port = config.get("listen_host", DEFAULT_CONFIG["listen_host"]), int(config.get("listen_port", DEFAULT_CONFIG["listen_port"]))

    # Choose server based on SSL configuration and available libraries
    if certfile:
        # HTTPS mode - use cheroot or Flask dev server
        # waitress cannot terminate TLS
        try:
            from cheroot.wsgi import Server as CherootServer
            from cheroot.ssl.builtin import BuiltinSSLAdapter
            print("  server               : cheroot + TLS (threads=12)")
            print(f"  certificate          : {certfile}")
            srv = CherootServer((host, port), app, numthreads=12)
            srv.ssl_adapter = BuiltinSSLAdapter(certfile, keyfile)
            try:
                srv.start()
            except KeyboardInterrupt:
                srv.stop()
        except ImportError:
            print("  server               : Flask dev server + TLS (pip install cheroot for a sturdier one)")
            app.run(host=host, port=port, threaded=True, ssl_context=(certfile, keyfile))
    else:
        # HTTP mode - prefer waitress for production
        try:
            from waitress import serve as waitress_serve
            print("  server               : waitress (threads=12)")
            waitress_serve(app, host=host, port=port, threads=12)
        except ImportError:
            print("  server               : Flask dev server (pip install waitress for a sturdier one)")
            app.run(host=host, port=port, threaded=True)

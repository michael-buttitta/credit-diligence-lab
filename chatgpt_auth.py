"""Local ChatGPT OAuth connection for Credit Diligence Lab.

Protocol https://developers.openai.com/siwc/token-sharing-open-source/sign-in
Credentials are DPAPI-encrypted on Windows and owner-only on Unix.
"""

import base64
import ctypes
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
import uuid
import webbrowser

import jwt
from filelock import FileLock

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
REVOKE_URL = ISSUER + "/api/accounts/oauth/revoke"
RESOURCE = "https://api.openai.com/v1"
USAGE_URL = "https://chatgpt.com/settings/usage"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
STORAGE = Path(__file__).resolve().parent / ".chatgpt"


def protect(data, decrypt=False):
    """Use the current Windows user's DPAPI key, without an interactive prompt."""
    if os.name != "nt":
        return data

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.c_void_p)]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.c_void_p))
    target = Blob()
    crypt = ctypes.windll.crypt32
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob),
    ]
    operation.restype = ctypes.c_int
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError("Windows could not protect or unlock the saved ChatGPT connection.")
    local_free = ctypes.windll.kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        local_free(target.data)


def json_request(url, form=None, token=None):
    headers = {"Accept": "application/json"}
    data = None
    if form is not None:
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request(url, data=data, headers=headers), timeout=30) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except HTTPError as error:
        with error:
            try:
                detail = json.load(error).get("error", "unknown_error")
                code = detail.get("code", "unknown_error") if isinstance(detail, dict) else detail
            except (ValueError, AttributeError):
                code = "unknown_error"
        raise RuntimeError(f"ChatGPT returned HTTP {error.code} ({code}).") from None


def verify_identity(id_token, client_id, nonce=None, subject=None, received_at=None):
    try:
        key = jwt.PyJWKClient(ISSUER + "/.well-known/jwks.json", timeout=20).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token, key.key, algorithms=["RS256"], issuer=ISSUER, audience=client_id,
            options={
                "require": ["sub", "exp", "iat", "iss", "aud"],
                "verify_exp": False, "verify_iat": False, "verify_nbf": False,
            },
        )
    except jwt.PyJWTError:
        raise RuntimeError("ChatGPT identity could not be verified. Run --login again.") from None
    checked_at = received_at if received_at is not None else time.time()
    if claims["exp"] < checked_at - 5 or claims["iat"] > checked_at + 5 or claims.get("nbf", 0) > checked_at + 5:
        raise RuntimeError("ChatGPT identity token has invalid timestamps.")
    if nonce is not None and (not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(claims["nonce"], nonce)):
        raise RuntimeError("ChatGPT identity nonce did not match.")
    if subject is not None and claims["sub"] != subject:
        raise RuntimeError("ChatGPT returned a different account. Add it as a separate connection.")
    return claims


class CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # Callback URLs contain a one-time authorization code.

    def do_GET(self):
        parsed = urlsplit(self.path)
        values = parse_qs(parsed.query, keep_blank_values=True)
        state = values.get("state", [])
        if parsed.path != "/auth/callback" or len(state) != 1 or not secrets.compare_digest(state[0], self.server.oauth_state):
            self.send_error(400, "Invalid sign-in callback")
            return
        if any(len(value) != 1 for value in values.values()):
            self.send_error(400, "Duplicate callback parameters")
            return
        self.server.oauth_result = {key: value[0] for key, value in values.items()}
        message = b"Return to Credit Diligence Lab to finish connecting."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(message)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(message)


class ChatGPTConnection:
    def __init__(self, directory=STORAGE):
        self.directory = directory
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise RuntimeError("ChatGPT storage must be a private directory.")
        if os.name != "nt":
            self.directory.chmod(0o700)
        self.filename = directory / "connection.bin"
        self.lock = FileLock(directory / "connection.lock", timeout=1)

    def read(self):
        if not self.filename.exists():
            return {"host_id": f"urn:uuid:{uuid.uuid4()}", "accounts": [], "active": None}
        if os.name != "nt" and self.filename.stat().st_mode & 0o077:
            raise RuntimeError("ChatGPT credential file must have owner-only permissions.")
        return json.loads(protect(self.filename.read_bytes(), decrypt=True))

    def save(self, state):
        data = protect(json.dumps(state).encode())
        descriptor, path = tempfile.mkstemp(dir=self.directory, prefix=".connection-")
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(path, self.filename)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def login(self, new_account=False):
        with self.lock:
            state = self.read()
            self.save(state)  # Persist the host ID before opening authorization.
            account = next((item for item in state["accounts"] if item["id"] == state["active"]), None)
            if new_account:
                account = None
            if account is None and not new_account and state["accounts"]:
                account = state["accounts"][-1]  # Resume an incomplete registration.
            client_id = account["client_id"] if account else "dynamic_agent_client"
            nonce, oauth_state, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            with HTTPServer(("127.0.0.1", 0), CallbackHandler) as server:
                server.timeout = 1
                server.oauth_state = oauth_state
                server.oauth_result = None
                redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
                parameters = {
                    "client_id": client_id, "ext_agent_host_id": state["host_id"],
                    "response_type": "code", "redirect_uri": redirect, "scope": SCOPES,
                    "resource": RESOURCE, "state": oauth_state, "nonce": nonce,
                    "code_challenge_method": "S256", "code_challenge": challenge,
                }
                if account is None:
                    parameters["agent_name_hint"] = "Credit Diligence Lab"
                print("Continue with ChatGPT in your browser. Allow this app to use your ChatGPT plan.", flush=True)
                if not webbrowser.open(AUTHORIZE_URL + "?" + urlencode(parameters)):
                    raise RuntimeError("Could not open the system browser for ChatGPT sign-in.")
                deadline = time.monotonic() + 300
                while server.oauth_result is None and time.monotonic() < deadline:
                    server.handle_request()
                callback = server.oauth_result
            if callback is None:
                raise RuntimeError("ChatGPT sign-in timed out. Run --login to try again.")
            if callback.get("error"):
                raise RuntimeError("ChatGPT sign-in was declined or failed.")
            issued_id = callback.get("client_id", client_id)
            if not issued_id or issued_id == "dynamic_agent_client" or (account and issued_id != client_id):
                raise RuntimeError("ChatGPT returned an invalid client registration.")
            if not callback.get("code"):
                raise RuntimeError("ChatGPT returned no authorization code.")
            if account is None:
                account = {"id": str(uuid.uuid4()), "client_id": issued_id}
                state["accounts"].append(account)
            self.save(state)  # Retain the issued ID even if code exchange fails.
            tokens = json_request(TOKEN_URL, {
                "grant_type": "authorization_code", "client_id": issued_id,
                "code": callback["code"], "code_verifier": verifier,
                "redirect_uri": redirect, "resource": RESOURCE,
            })
            claims = verify_identity(
                tokens["id_token"], issued_id, nonce=nonce, subject=account.get("subject"),
            )
            self.update_tokens(account, tokens, claims)
            state["active"] = account["id"]
            self.save(state)
            self.require_permission(account)
            print(f"Connected. Using ChatGPT plan. Manage usage: {USAGE_URL}", flush=True)

    @staticmethod
    def update_tokens(account, tokens, claims):
        if tokens.get("token_type", "").lower() != "bearer" or not tokens.get("access_token"):
            raise RuntimeError("ChatGPT returned no usable bearer credential.")
        expires_in = tokens["expires_in"]
        if not isinstance(expires_in, (int, float)) or expires_in <= 0:
            raise RuntimeError("ChatGPT returned an invalid token lifetime.")
        tokens = dict(tokens)
        tokens.setdefault("scope", " ".join(account.get("scopes", [])))
        if "refresh_token" not in tokens and account.get("tokens", {}).get("refresh_token"):
            tokens["refresh_token"] = account["tokens"]["refresh_token"]
        account.update(
            subject=claims["sub"], email=claims.get("email", ""),
            tokens=tokens, scopes=tokens.get("scope", "").split(),
            expires_at=time.time() + expires_in,
        )
        account.pop("pending_refresh", None)

    @staticmethod
    def require_permission(account):
        if not {"resource.invoke", "chatgpt.tokens.use.direct"}.issubset(account.get("scopes", [])):
            raise RuntimeError(f"ChatGPT plan usage needs authorization. Manage usage: {USAGE_URL}")

    def access_token(self):
        with self.lock:
            state = self.read()
            account = next((item for item in state["accounts"] if item["id"] == state["active"]), None)
            if not account or not account.get("tokens"):
                raise RuntimeError("Run analyze.py --login to connect your ChatGPT plan.")
            self.require_permission(account)
            if account.get("pending_refresh") or account["expires_at"] <= time.time() + 60:
                pending = account.get("pending_refresh")
                if pending is None:
                    old = account["tokens"]
                    if not old.get("refresh_token"):
                        raise RuntimeError("ChatGPT connection expired. Run --login again.")
                    tokens = json_request(TOKEN_URL, {
                        "grant_type": "refresh_token", "client_id": account["client_id"],
                        "refresh_token": old["refresh_token"], "resource": RESOURCE,
                    })
                    pending = {"tokens": tokens, "received_at": time.time()}
                    account["pending_refresh"] = pending
                    self.save(state)  # Preserve a rotated token if JWKS verification is interrupted.
                claims = verify_identity(
                    pending["tokens"]["id_token"], account["client_id"],
                    subject=account["subject"], received_at=pending["received_at"],
                )
                self.update_tokens(account, pending["tokens"], claims)
                account["expires_at"] = pending["received_at"] + pending["tokens"]["expires_in"]
                self.save(state)
                self.require_permission(account)
                if account["expires_at"] <= time.time():
                    raise RuntimeError("ChatGPT connection recovered. Retry to renew its access token.")
            return account["tokens"]["access_token"]

    def accounts(self):
        with self.lock:
            state = self.read()
            return [
                {"id": item["id"], "email": item.get("email", ""), "active": item["id"] == state["active"]}
                for item in state["accounts"]
            ]

    def select(self, account_id):
        with self.lock:
            state = self.read()
            if not any(item["id"] == account_id for item in state["accounts"]):
                raise RuntimeError("Unknown ChatGPT connection. Use --accounts to list saved connections.")
            state["active"] = account_id
            self.save(state)

    def logout(self):
        with self.lock:
            state = self.read()
            account = next((item for item in state["accounts"] if item["id"] == state["active"]), None)
            if account:
                tokens = account.get("pending_refresh", {}).get("tokens", account.get("tokens", {}))
                if tokens.get("refresh_token"):
                    json_request(REVOKE_URL, {
                        "token": tokens["refresh_token"], "token_type_hint": "refresh_token",
                        "client_id": account["client_id"],
                    })
                for key in ("tokens", "pending_refresh", "scopes", "expires_at"):
                    account.pop(key, None)
                self.save(state)

    def models(self):
        catalog = json_request(RESOURCE + "/models", token=self.access_token())
        return [item for item in catalog["models"] if item.get("visibility") == "list"]

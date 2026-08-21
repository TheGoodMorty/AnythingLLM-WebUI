# AnythingLLM Web Console

A self-hosted web interface for your AnythingLLM instance. Built to solve the mobile/desktop pairing issues that some users experience with the official apps.

**Why this exists:** The official AnythingLLM mobile and desktop apps sometimes fail to pair correctly. This web console provides a reliable alternative that works on any device with a browser — phones, tablets, laptops, or desktops.

## Quick Start

```bash
# Install dependencies
pip install flask requests

# Run the server
python server.py
```

Then open:
- **Local:** `http://localhost:1555`
- **LAN:** `http://YOUR_IP:1555` (from other devices on your network)
- **WAN:** Your port-forwarded address (if configured)

Create your first account with your AnythingLLM API key. The server validates it against your instance before creating the account.

## What It Does

This console:
- ✅ Serves a modern, responsive web UI (works great on mobile)
- ✅ Manages user accounts locally (username + password + API key)
- ✅ Proxies all API requests through the server (API keys never touch the browser)
- ✅ Supports multiple users with isolated workspaces
- ✅ Installable as a PWA (add to home screen on phones)

## Configuration

On first run, `config.json` is created with these settings:

| Setting | Default | Description |
| --- | --- | --- |
| `anythingllm_url` | `http://localhost:3001` | Where the **server** reaches AnythingLLM. Keep as `localhost:3001` even for remote access. |
| `listen_host` | `0.0.0.0` | Network interface to bind. `0.0.0.0` = all interfaces, `127.0.0.1` = localhost only. |
| `listen_port` | `1555` | Port number for the web console. |
| `master_api_key` | *(pre-filled)* | Optional admin key. See "Master API Key" section below. |
| `allow_signup` | `true` | Set to `false` to prevent new account creation after setup. |
| `ssl_certfile` | `""` | Path to SSL certificate PEM file (optional, for HTTPS). |
| `ssl_keyfile` | `""` | Path to SSL key PEM file (optional, for HTTPS). |

### About the Master API Key

**Completely optional** — only needed for multi-user management.

**What it does:** Grants access to the Settings page where you can:
- View and delete other user accounts
- Change server settings (URL, port, SSL)
- Rotate the master key itself

**When you need it:**
- **Single user?** Ignore it or delete it — all features work without it
- **Multiple users?** Set it so one account can manage others

**How it works:** Your account's AnythingLLM API key must match the `master_api_key` in `config.json`. To make your account the master:

```json
{
  "master_api_key": "YOUR-ACTUAL-ANYTHINGLLM-API-KEY"
}
```

Restart the server after changing this value.

**To disable master access entirely:**
```json
{
  "master_api_key": ""
}
```

## HTTPS Setup (Required for Internet Access)

### Option 1: Built-in SSL

Edit `config.json`:
```json
{
  "ssl_certfile": "/path/to/cert.pem",
  "ssl_keyfile": "/path/to/key.pem"
}
```

Restart — the server auto-detects and uses HTTPS if files exist.

**Free certificates:**
- [Let's Encrypt](https://letsencrypt.org/) — auto-renewing
- [ZeroSSL](https://zerossl.com/) — 90-day certs
- [Cloudflare Origin CA](https://www.cloudflare.com/ssl/origin-ca/) — long-lived

### Option 2: Reverse Proxy (Recommended)

Run the console on HTTP, let Caddy/nginx handle HTTPS.

**Caddy** (simplest, auto-renewal):
```Caddyfile
console.your-domain.com {
    reverse_proxy localhost:1555
}
```

**nginx**:
```nginx
server {
    listen 443 ssl;
    server_name console.your-domain.com;
    
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
    
    location / {
        proxy_pass http://localhost:1555;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

## UI Features & Panels

### Main Chat Interface
- **Workspaces sidebar** — Accordion-style navigation with search/filter
- **Chat pane** — Full conversation history with copy/retry/edit functions
- **Composer** — Drag-and-drop file attachments, voice dictation, streaming toggle
- **Mobile responsive** — Sidebar becomes a drawer on small screens

### Workspace Panel (⚙️ button or 3-dots menu)
- Rename workspace
- Change chat mode (chat/query/agent)
- Adjust temperature, history length, top-N snippets
- Set similarity threshold for embeddings
- Customize system prompt and query refusal response
- **Reset chats** — Delete ALL conversations in this workspace (red button)
- **Duplicate** — Copy workspace with all settings and documents
- **View all chats** — Export conversations
- Manage embedded documents (pin/unpin, remove)
- Delete workspace

### Documents Panel
**Browse tab:**
- View all folders and files in AnythingLLM storage
- Select multiple documents for batch operations
- Expand/collapse folders
- Embed documents into workspaces
- Move documents between folders
- Delete documents or entire folders

**Add tab:**
- Upload files from your computer
- Scrape and embed URLs
- Paste raw text as a document
- Set metadata (title, author)
- Choose target folder and workspaces

**Move & Purge tab:**
- Raw JSON interface for bulk file operations
- Move multiple files at once
- Permanently delete files from storage

### Search Panel
- Vector similarity search within a workspace
- Adjustable top-N results and minimum score threshold
- View matched chunks with scores

### System Panel
- View AnythingLLM system settings
- Check vector count and provider info
- Update environment variables (advanced)
- Export all chats (JSON, CSV, JSONL, Alpaca format)
- Dump settings to file storage

### Admin Panel (Multi-user instances only)
**Users tab:**
- Create new users with role assignment
- Edit user passwords
- Suspend/unsuspend users
- Delete users

**Invites tab:**
- Create invite codes for user registration
- Deactivate existing invites

**Access tab:**
- Manage workspace permissions per user
- Grant/revoke workspace access
- Reset access lists

**Chats tab:**
- View all workspace chats across all users
- Paginated loading

**Prefs tab:**
- Update multi-user preferences
- Set support email

### Embeds Panel
- Create embeddable chat widgets for websites
- Configure allowed domains, rate limits
- Toggle embeds on/off
- View embed chat history
- Get embed code snippets

### OpenAI Panel
- Test OpenAI-compatible endpoints
- Send chat completions
- Generate embeddings
- View available models and vector stores

### Voice Panel
- Transcribe audio/video files
- Record voice messages directly
- Append transcripts to chat

### Files Panel
- View all agent-generated files (PDFs, DOCX, etc.)
- Download files by storage filename
- Track files seen in chat responses
- Clear file history

### API Console (Raw API tab)
**For advanced users and testing:**
- Execute ANY AnythingLLM API endpoint manually
- Pre-filled templates for common endpoints
- Select HTTP method (GET/POST/PUT/PATCH/DELETE)
- Edit request path with auto-fill for `{slug}` and `{threadSlug}`
- Write custom JSON bodies
- View raw responses with status codes
- Fill placeholders from current selection

**Common use cases:**
- Test new API endpoints before they're added to the UI
- Debug API issues with full control over requests
- Access deprecated endpoints (like `update-users`)
- Experiment with bulk operations
- Learn the AnythingLLM API structure

**Example workflow:**
1. Select endpoint from dropdown (e.g., "Workspaces → GET /v1/workspace/{slug}")
2. Path auto-fills with current workspace slug
3. Click "Send request"
4. View raw JSON response
5. Modify and re-run as needed

## Security

- **API keys never leave the server** — Browser only sees chat content
- **Passwords hashed** — PBKDF2 with 240,000 iterations
- **Session security** — HttpOnly + SameSite cookies
- **Rate limiting** — 5 failed login attempts per 60 seconds
- **File access control** — Only safe extensions served, path traversal blocked
- **SSRF protection** — API key validation only against configured instance

**Important:** The master key in `config.json` is stored in plain text. Keep it secure and never commit it to version control.

## Known Limitations

1. **API paths assumed** — Uses standard `/api/v1/...` routes. If your AnythingLLM uses different paths, edit `endpointCatalog()` in the HTML file.

2. **Agent files indexed per-browser** — No API endpoint to list generated files, so the Files panel tracks them locally. If AnythingLLM clears storage, links break until you forget the entry.

3. **Plain text responses** — Assistant replies show fenced code blocks but no markdown tables, headings, or inline links yet.

4. **No per-message editing** — API doesn't support editing/deleting individual messages. "Edit" loads prompt back into composer (creates new turn). "Retry" re-sends the prompt. Only "Reset" clears thread history.

5. **Document name format** — `GET /v1/document/{docName}` expects just the filename (last path segment), which the UI handles correctly.

6. **No env key picker** — `update-env` is a free-form field because valid keys depend on your provider configuration.

7. **Deprecated endpoints intentional** — `update-users` is only in the API console, on purpose.

8. **Embed snippet URL** — Generated from `instanceUrl`. Edit the snippet if your widget is served from a different host.

9. **Multi-user mode detection** — Endpoints are always attempted; 401 errors shown as banners if not in multi-user mode.

## Customization Ideas

Want to extend this? Here are some suggestions:

- **Per-device session partitioning** — Separate workspace chat history per device
- **Auto-refresh sidebar** — Poll or refresh on window focus
- **Cmd-K switcher** — Quick jump between workspaces/threads
- **Markdown renderer** — Full markdown support for assistant replies
- **Invite links** — Generate full URLs from invite codes
- **Per-workspace chat export** — Client-side CSV of current thread
- **Systemd service** — Run as a background service with auto-start
- **Docker container** — Containerize for easy deployment
- **Dark/light theme sync** — Match system theme automatically
- **Notification support** — Browser notifications for long-running responses

## File Structure

```
server.py                      # Flask server (570 lines, well-commented)
AnythingLLM Console.dc.html    # UI template + JavaScript logic
support.js                     # DC runtime (required, don't modify)
config.json                    # Server configuration (auto-created)
users.json                     # User accounts (auto-created)
manifest.webmanifest           # PWA manifest
sw.js                          # Service worker for offline support
icon-192.png, icon-512.png     # App icons
README.md                      # This file
AnythingLLM-Developer-API.md   # AnythingLLM API reference
start_ALLM_webUI.ps1          # PowerShell launcher script
```

## Troubleshooting

**Server won't start:**
- Check if port 1555 is already in use
- Verify `config.json` is valid JSON
- Ensure Python 3.6+ is installed

**Can't connect to AnythingLLM:**
- Verify `anythingllm_url` in `config.json` is correct
- Check if AnythingLLM is running on port 3001
- Try `http://localhost:3001` even for remote instances (server-side proxy)

**Mobile pairing issues:**
- This console IS the solution! Use it instead of the official mobile app
- Access from phone browser at `http://YOUR_DESKTOP_IP:1555`
- Add to home screen for app-like experience

**API errors:**
- Check browser console for detailed error messages
- Verify your AnythingLLM API key is valid
- Try the API Console panel to test endpoints manually

## License & Credits

Built for the AnythingLLM community. Share freely and improve as needed.

**Special thanks:** AnythingLLM team for the excellent API documentation that made this possible.

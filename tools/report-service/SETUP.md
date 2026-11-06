# Problem-report upload service: setup

A one-button **Send a problem report** in hamdash (Settings → Help), for
operators who have no GitHub account. The report goes to a small Cloudflare
Worker, which stores it in a **private** GitHub repository and opens an issue
there, so you are notified and a Claude session can fetch it with `gh`.

```
hamdash  ──HTTPS upload──▶  Cloudflare Worker  ──GitHub API──▶  wizzardsl/hamdash-reports (private)
                            (checks size and contents,          zip committed + issue opened
                             holds the GitHub key)
```

Cost: nothing. Cloudflare's free plan allows 100,000 requests a day; private
repositories are free. Time: about 15 minutes.

**You do steps 1 to 4. Claude does the code and the deploy, then step 6 is
yours again.** The GitHub key goes straight from your clipboard into
Cloudflare; it is never pasted into a chat or written into the repository.

---

## 1. The private repository for reports

1. Go to <https://github.com/new>.
2. **Repository name:** `hamdash-reports`
3. Choose **Private**.
4. Tick **Add a README file** (the upload needs a branch to exist).
5. **Create repository**.

## 2. A GitHub key that can only touch that repository

1. Go to <https://github.com/settings/personal-access-tokens/new>
   (Settings → Developer settings → Personal access tokens →
   **Fine-grained tokens** → Generate new token).
2. **Token name:** `hamdash report uploader`
3. **Expiration:** one year. Put a reminder in your calendar a week before -
   when it expires, uploads stop until you make a new one (step 6 again).
4. **Repository access:** **Only select repositories** → `hamdash-reports`.
5. **Permissions → Repository permissions:**
   - **Contents:** Read and write
   - **Issues:** Read and write
   - (Metadata: Read-only is added by itself.)
6. **Generate token**, then **copy it** - GitHub shows it only once. Keep it
   on your clipboard or in your password manager until step 6. Do not send
   it to Claude.

## 3. A Cloudflare account

1. Sign up at <https://dash.cloudflare.com/sign-up> (the free plan) and verify
   your email.
2. In the dashboard open **Workers & Pages**. The first time, Cloudflare asks
   you to choose a **workers.dev subdomain** - something like `va7sll`. The
   service will live at `https://hamdash-reports.<your-subdomain>.workers.dev`.
3. No domain name is needed, and no payment method.

## 4. Let this PC deploy to your Cloudflare account

In a terminal on the station (Ubuntu's Node comes without `npm`/`npx`;
`sudo apt install npm` adds them):

```bash
cd ~/Projects/hamdash/tools/report-service
npx wrangler login
```

A browser window opens asking you to allow Wrangler (Cloudflare's own tool)
into your account - **Allow**. That is the only sign-in; it stays on this PC.

**Then tell Claude:** "Cloudflare is set up, my subdomain is ___."

## 5. Claude writes and deploys the Worker

**Done 2026-11-06: deployed at https://hamdash-reports.wizzardsl.workers.dev,
key set, a test report arrived as issue #1.** Claude added the Worker in
this folder (`worker.js`, `wrangler.toml`), its tests
(`tests/js/report-worker.test.js`, `tests/test_report_send.py`), and the
hamdash side - the Send button, the dialog showing what is in
the report, the reference code, and keeping the zip if the internet is down -
then ran `npx wrangler deploy`. **To change it:** edit `worker.js`, run the tests, then `npx wrangler deploy` here.

What the Worker will do:

- accept a POST of one hamdash report zip, **at most 25 MB**, and refuse
  anything that is not one (it has to contain hamdash's own manifest);
- limit how often one address can send, so the public URL cannot be used to
  flood the repository;
- stream the zip, untouched, to an asset on that month's release
  (`reports-YYYY-MM`) in `hamdash-reports` - not a commit, because a commit
  needs the file re-encoded, and the free plan's 10 ms of CPU a request is
  not enough for a large report - and open an issue titled with the
  reference, version, platform and radio, linking the zip and quoting the
  operator's note if they wrote one;
- answer the app with the reference code (e.g. `HD-7F3K`);
- once a day, delete reports older than **90 days**.

## 6. Give the Worker its GitHub key

After Claude says the Worker is deployed:

```bash
cd ~/Projects/hamdash/tools/report-service
npx wrangler secret put GITHUB_TOKEN
```

Paste the key from step 2 when it asks, and press Enter. It is stored
encrypted in Cloudflare and nowhere else. Then tell Claude, who sends a test
report and checks it arrives as an issue.

---

## Afterwards

- **Fetching a report** (or have Claude do it): the issue names it, e.g.
  `gh release download reports-2026-11 -R wizzardsl/hamdash-reports -p HD-PCAV3.zip`.

- **New reports** arrive as issues in `hamdash-reports`; GitHub notifies you
  as for any issue (Settings → Notifications decides how).
- **Renewing the key:** make a new one as in step 2, then step 6 again.
- **Turning the service off:** Cloudflare dashboard → Workers & Pages →
  `hamdash-reports` → Settings → Delete. hamdash then says the upload failed
  and keeps the report on disk, as it does today.
- **Privacy:** reports hold the operator's callsign, grid and logs (passwords
  and API keys are removed before anything is written). Only you can see the
  repository. hamdash's docs and SECURITY.md will say exactly this, and that
  nothing is sent unless the operator presses Send.

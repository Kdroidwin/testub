# X badware domain filter

The GitHub Actions workflow reads recent posts from `@masaomi346` through `twitter-api-safe-relay`, extracts URL hostnames, and writes one uBlock Origin rule per line to the repository root file `ublockoriginbadwarefromx.txt`, such as `||example.com^`. It normalizes common defanged forms (`hxxps://`, escaped slashes, and `\.`), deduplicates hostnames, and excludes `virustotal.com` and `urlscan.io` including their subdomains.

It runs every six hours or manually from **Actions → Update X badware domains → Run workflow**. It reads up to 400 posts each run and commits the list only when it changes. The initial file was seeded from the example posts supplied for this setup; after relay secrets are configured, the workflow replaces it with the fetched list.

## Required setup

The relay must have a browser profile logged in to X, and GitHub Actions must be able to reach its URL. A GitHub-hosted runner cannot reach a relay available only on a personal computer or private home network. Use a publicly reachable relay protected by a reverse proxy, or a self-hosted Actions runner that can reach the relay.

Add these under **Settings → Secrets and variables → Actions**:

- `TWITTER_RELAY_BASE_URL`: relay URL, without a trailing slash.
- `TWITTER_RELAY_BEARER_TOKEN` (optional): only when the reverse proxy validates this token; the relay itself does not authenticate the header.
- `TWITTER_RELAY_PROFILE` (optional): profile name when the relay has multiple profiles.

The script downloads current `UserByScreenName` and `UserTweets` GraphQL request definitions at run time from the relay skills catalog and resolves the account ID dynamically.

/**
 * Enrich self-emailed tweet links to their ultimate content.
 *
 * Reads `data/tweets.csv` (produced by `scripts/mine_tweets.py`) and writes
 * `data/tweets_enriched.jsonl` — one JSON record per email (`message_id`),
 * accumulating each hop of the link chain:
 *
 *   source_urls  → resolved (t.co map) → tweet (intermediate metadata)
 *                → ultimate (extracted final content)
 *
 * Deterministic data plumbing only. Tweet metadata comes from the keyless
 * fxtwitter JSON API (Twitter syndication fallback) because x.com blocks
 * generic scraping; the heterogeneous *outbound* content is extracted with
 * pi-web-access's `extractContent` (Readability / GitHub clone / YouTube /
 * PDF / Jina-Reader fallback).
 *
 * Resumable: existing records are skipped (re-run continues where it stopped).
 * Every fetch is wrapped so one bad link never aborts the batch — failures are
 * recorded as `fetch_status:"error"` entries.
 *
 * Run via `just enrich` / `npx tsx scripts/enrich_tweets.ts`.
 */

import { readFileSync, appendFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { extractContent } from "pi-web-access/extract.ts";

const SCHEMA_VERSION = 1;
const SELF_TWEET_HOST_RE = /(^|\.)(x\.com|twitter\.com)$/i;
const STATUS_RE = /(?:x\.com|twitter\.com)\/(?:i\/web\/status|[^/]+\/status)\/(\d+)/i;
const BROWSER_UA =
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface EmailGroup {
	message_id: string;
	date: string;
	subject: string;
	urls: string[];
}

interface TweetMeta {
	permalink: string | null;
	tweet_id: string | null;
	author: string | null;
	text: string;
	outbound_links: string[];
	fetch_status: "ok" | "unavailable";
	source: "fxtwitter" | "syndication" | "none";
}

interface UltimateEntry {
	url: string;
	type: "article" | "youtube" | "github" | "pdf" | "tweet" | "other";
	title: string;
	content: string;
	fetch_status: "ok" | "error";
	error: string | null;
}

interface ResolvedEntry {
	short: string;
	final: string;
}

interface EnrichedRecord {
	message_id: string;
	date: string;
	subject: string;
	source_urls: string[];
	tweet: TweetMeta;
	resolved: ResolvedEntry[];
	ultimate: UltimateEntry[];
	fetched_at: string;
	schema_version: number;
}

// ---------------------------------------------------------------------------
// CSV parsing (RFC-4180-ish: quoted fields may contain commas and newlines)
// ---------------------------------------------------------------------------

interface CsvRow {
	date: string;
	url: string;
	message_id: string;
	subject: string;
}

/** Parse the 4-column tweets.csv into rows, honouring quoted fields. */
export function parseTweetsCsv(path: string): CsvRow[] {
	const text = readFileSync(path, "utf-8");
	const records = parseCsv(text);
	const rows: CsvRow[] = [];
	for (let i = 0; i < records.length; i++) {
		const fields = records[i];
		// Skip the header row.
		if (i === 0 && fields[0] === "date" && fields[1] === "url") continue;
		if (fields.length < 4) continue;
		rows.push({
			date: fields[0],
			url: fields[1],
			message_id: fields[2],
			subject: fields[3],
		});
	}
	return rows;
}

/** Minimal RFC-4180 CSV reader → array of field arrays. */
function parseCsv(text: string): string[][] {
	const rows: string[][] = [];
	let field = "";
	let row: string[] = [];
	let inQuotes = false;
	let i = 0;
	while (i < text.length) {
		const ch = text[i];
		if (inQuotes) {
			if (ch === '"') {
				if (text[i + 1] === '"') {
					field += '"';
					i += 2;
					continue;
				}
				inQuotes = false;
				i++;
				continue;
			}
			field += ch;
			i++;
			continue;
		}
		if (ch === '"') {
			inQuotes = true;
			i++;
			continue;
		}
		if (ch === ",") {
			row.push(field);
			field = "";
			i++;
			continue;
		}
		if (ch === "\r") {
			i++;
			continue;
		}
		if (ch === "\n") {
			row.push(field);
			rows.push(row);
			row = [];
			field = "";
			i++;
			continue;
		}
		field += ch;
		i++;
	}
	// Flush trailing field/row (file may not end with a newline).
	if (field.length > 0 || row.length > 0) {
		row.push(field);
		rows.push(row);
	}
	return rows;
}

/** Group CSV rows by message_id, deduping urls while preserving order. */
export function groupByMessage(rows: CsvRow[]): EmailGroup[] {
	const map = new Map<string, EmailGroup>();
	for (const r of rows) {
		let g = map.get(r.message_id);
		if (g === undefined) {
			g = { message_id: r.message_id, date: r.date, subject: r.subject, urls: [] };
			map.set(r.message_id, g);
		}
		if (!g.urls.includes(r.url)) g.urls.push(r.url);
	}
	return [...map.values()];
}

// ---------------------------------------------------------------------------
// Link resolution
// ---------------------------------------------------------------------------

/** Follow a (t.co or any) URL to its final destination. HEAD then GET. */
export async function resolveShortlink(url: string, timeoutMs = 15000): Promise<string> {
	for (const method of ["HEAD", "GET"] as const) {
		try {
			const res = await fetch(url, {
				method,
				redirect: "follow",
				headers: { "User-Agent": BROWSER_UA },
				signal: AbortSignal.timeout(timeoutMs),
			});
			if (res.url) return res.url;
		} catch {
			// fall through to next method / return original
		}
	}
	return url;
}

function isTwitterHost(url: string): boolean {
	try {
		return SELF_TWEET_HOST_RE.test(new URL(url).hostname);
	} catch {
		return false;
	}
}

function extractTweetId(url: string): string | null {
	const m = url.match(STATUS_RE);
	return m ? m[1] : null;
}

/**
 * Choose the x.com/twitter.com status permalink for an email, resolving t.co
 * shortlinks when the permalink isn't present directly. Populates `resolved`.
 */
export async function pickPermalink(
	urls: string[],
	resolved: ResolvedEntry[],
): Promise<{ permalink: string | null; tweet_id: string | null }> {
	// Direct hit first.
	for (const url of urls) {
		const id = extractTweetId(url);
		if (id !== null && isTwitterHost(url)) {
			return { permalink: stripStatusUrl(url, id), tweet_id: id };
		}
	}
	// Otherwise resolve t.co shortlinks and look for a status permalink.
	for (const url of urls) {
		if (!/\bt\.co\//i.test(url)) continue;
		const final = await resolveShortlink(url);
		resolved.push({ short: url, final });
		const id = extractTweetId(final);
		if (id !== null && isTwitterHost(final)) {
			return { permalink: stripStatusUrl(final, id), tweet_id: id };
		}
	}
	return { permalink: null, tweet_id: null };
}

function stripStatusUrl(url: string, id: string): string {
	try {
		const u = new URL(url);
		const m = u.pathname.match(/\/([^/]+)\/status\/\d+/i);
		const user = m ? m[1] : "i";
		return `https://x.com/${user}/status/${id}`;
	} catch {
		return `https://x.com/i/status/${id}`;
	}
}

// ---------------------------------------------------------------------------
// Tweet metadata (fxtwitter → syndication fallback)
// ---------------------------------------------------------------------------

/** Fetch tweet author/text/outbound links. Degrades to "unavailable". */
export async function fetchTweetMeta(
	permalink: string | null,
	tweetId: string | null,
	subject: string,
	timeoutMs = 15000,
): Promise<TweetMeta> {
	const base: TweetMeta = {
		permalink,
		tweet_id: tweetId,
		author: null,
		text: subject,
		outbound_links: [],
		fetch_status: "unavailable",
		source: "none",
	};
	if (tweetId === null) return base;

	// 1. fxtwitter
	try {
		const res = await fetch(`https://api.fxtwitter.com/i/status/${tweetId}`, {
			headers: { "User-Agent": BROWSER_UA, Accept: "application/json" },
			signal: AbortSignal.timeout(timeoutMs),
		});
		if (res.ok) {
			const data = (await res.json()) as any;
			const t = data?.tweet;
			if (t) {
				const facets = t?.raw_text?.facets ?? [];
				const links = facets
					.filter((f: any) => f?.type === "url" && typeof f.replacement === "string")
					.map((f: any) => f.replacement as string);
				return {
					permalink: t.url ?? permalink,
					tweet_id: tweetId,
					author: t.author?.screen_name ? `@${t.author.screen_name}` : null,
					text: t.text ?? subject,
					outbound_links: links,
					fetch_status: "ok",
					source: "fxtwitter",
				};
			}
		}
	} catch {
		// fall through to syndication
	}

	// 2. Twitter syndication fallback
	try {
		const token = syndicationToken(tweetId);
		const url = `https://cdn.syndication.twimg.com/tweet-result?id=${tweetId}&lang=en&token=${token}`;
		const res = await fetch(url, {
			headers: { "User-Agent": BROWSER_UA, Accept: "application/json" },
			signal: AbortSignal.timeout(timeoutMs),
		});
		if (res.ok) {
			const data = (await res.json()) as any;
			const text: string = data?.text ?? subject;
			const entityUrls: string[] = (data?.entities?.urls ?? [])
				.map((u: any) => u?.expanded_url)
				.filter((u: any) => typeof u === "string");
			return {
				permalink: permalink,
				tweet_id: tweetId,
				author: data?.user?.screen_name ? `@${data.user.screen_name}` : null,
				text,
				outbound_links: entityUrls,
				fetch_status: "ok",
				source: "syndication",
			};
		}
	} catch {
		// total failure → unavailable
	}

	return base;
}

/** Derive the syndication endpoint token from the tweet id. */
function syndicationToken(id: string): string {
	return ((Number(id) / 1e15) * Math.PI)
		.toString(6 ** 2)
		.replace(/(0+|\.)/g, "");
}

// ---------------------------------------------------------------------------
// Outbound link collection
// ---------------------------------------------------------------------------

/** Union of tweet-entity links and resolved non-self email t.co's, deduped. */
export function collectOutboundLinks(
	meta: TweetMeta,
	emailUrls: string[],
	resolved: ResolvedEntry[],
): string[] {
	const out: string[] = [];
	const push = (u: string) => {
		if (!u) return;
		if (isTwitterHost(u)) return; // drop x.com/twitter self-links
		if (!out.includes(u)) out.push(u);
	};
	for (const u of meta.outbound_links) push(u);
	// Resolved email t.co destinations that aren't the tweet itself.
	for (const r of resolved) push(r.final);
	// Non-twitter, non-t.co raw email URLs (rare, but possible).
	for (const u of emailUrls) {
		if (/\bt\.co\//i.test(u)) continue;
		push(u);
	}
	return out;
}

// ---------------------------------------------------------------------------
// Ultimate content extraction
// ---------------------------------------------------------------------------

function classifyType(url: string, content: string): UltimateEntry["type"] {
	let host = "";
	let path = "";
	try {
		const u = new URL(url);
		host = u.hostname.toLowerCase();
		path = u.pathname.toLowerCase();
	} catch {
		/* ignore */
	}
	if (/(^|\.)(youtube\.com|youtu\.be)$/.test(host)) return "youtube";
	if (host === "github.com" || host.endsWith(".github.com")) return "github";
	if (path.endsWith(".pdf")) return "pdf";
	if (isTwitterHost(url)) return "tweet";
	if (content && content.trim().length > 0) return "article";
	return "other";
}

/** Extract each outbound link via extractContent, bounded concurrency. */
export async function fetchUltimate(
	urls: string[],
	concurrency: number,
): Promise<UltimateEntry[]> {
	const results: UltimateEntry[] = new Array(urls.length);
	let next = 0;
	const worker = async () => {
		while (true) {
			const i = next++;
			if (i >= urls.length) break;
			const url = urls[i];
			try {
				const r = await extractContent(url);
				const errored = Boolean(r.error);
				results[i] = {
					url,
					type: classifyType(url, r.content ?? ""),
					title: r.title ?? "",
					content: r.content ?? "",
					fetch_status: errored ? "error" : "ok",
					error: r.error ?? null,
				};
			} catch (err) {
				results[i] = {
					url,
					type: classifyType(url, ""),
					title: "",
					content: "",
					fetch_status: "error",
					error: err instanceof Error ? err.message : String(err),
				};
			}
		}
	};
	const pool = Array.from({ length: Math.max(1, Math.min(concurrency, urls.length || 1)) }, worker);
	await Promise.all(pool);
	return results;
}

// ---------------------------------------------------------------------------
// Resumability / output
// ---------------------------------------------------------------------------

/** Set of message_ids already written (errors count as done unless refetching). */
export function loadDone(outPath: string, refetchErrors: boolean): Set<string> {
	const done = new Set<string>();
	if (!existsSync(outPath)) return done;
	const text = readFileSync(outPath, "utf-8");
	for (const line of text.split("\n")) {
		const trimmed = line.trim();
		if (!trimmed) continue;
		let rec: EnrichedRecord;
		try {
			rec = JSON.parse(trimmed) as EnrichedRecord;
		} catch {
			continue;
		}
		if (refetchErrors) {
			const hasError =
				rec.tweet?.fetch_status === "unavailable" ||
				(rec.ultimate ?? []).some((u) => u.fetch_status === "error");
			if (hasError) continue; // not done — will be reprocessed
		}
		done.add(rec.message_id);
	}
	return done;
}

/** Append one JSON line. Never rewrites the whole file. */
export function appendRecord(outPath: string, record: EnrichedRecord): void {
	mkdirSync(dirname(outPath), { recursive: true });
	appendFileSync(outPath, JSON.stringify(record) + "\n", "utf-8");
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

interface Args {
	in: string;
	out: string;
	limit: number | null;
	concurrency: number;
	refetchErrors: boolean;
	delayMs: number;
}

function parseArgs(argv: string[]): Args {
	const args: Args = {
		in: "data/tweets.csv",
		out: "data/tweets_enriched.jsonl",
		limit: null,
		concurrency: 4,
		refetchErrors: false,
		delayMs: 250,
	};
	for (let i = 0; i < argv.length; i++) {
		const a = argv[i];
		switch (a) {
			case "--in":
				args.in = argv[++i];
				break;
			case "--out":
				args.out = argv[++i];
				break;
			case "--limit":
				args.limit = Number(argv[++i]);
				break;
			case "--concurrency":
				args.concurrency = Number(argv[++i]);
				break;
			case "--refetch-errors":
				args.refetchErrors = true;
				break;
			case "--delay-ms":
				args.delayMs = Number(argv[++i]);
				break;
			default:
				throw new Error(`unknown argument: ${a}`);
		}
	}
	return args;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function main(): Promise<void> {
	const args = parseArgs(process.argv.slice(2));

	if (!existsSync(args.in)) {
		console.error(`error: input CSV not found: ${args.in} (run 'just mine' first)`);
		process.exit(1);
	}

	const rows = parseTweetsCsv(args.in);
	const groups = groupByMessage(rows);
	const done = loadDone(args.out, args.refetchErrors);

	const pending = groups.filter((g) => !done.has(g.message_id));
	const selected = args.limit !== null ? pending.slice(0, args.limit) : pending;

	console.error(
		`enrich: ${groups.length} messages, ${done.size} already done, ` +
			`${pending.length} pending, processing ${selected.length}`,
	);

	let processed = 0;
	let errors = 0;
	for (const g of selected) {
		const resolved: ResolvedEntry[] = [];
		const { permalink, tweet_id } = await pickPermalink(g.urls, resolved);
		const meta = await fetchTweetMeta(permalink, tweet_id, g.subject);
		const outbound = collectOutboundLinks(meta, g.urls, resolved);
		const ultimate = await fetchUltimate(outbound, args.concurrency);

		const record: EnrichedRecord = {
			message_id: g.message_id,
			date: g.date,
			subject: g.subject,
			source_urls: g.urls,
			tweet: meta,
			resolved,
			ultimate,
			fetched_at: new Date().toISOString(),
			schema_version: SCHEMA_VERSION,
		};
		appendRecord(args.out, record);

		processed++;
		const hadError =
			meta.fetch_status === "unavailable" || ultimate.some((u) => u.fetch_status === "error");
		if (hadError) errors++;
		console.error(
			`  [${processed}/${selected.length}] ${g.message_id} ` +
				`tweet=${meta.fetch_status}(${meta.source}) outbound=${outbound.length} ` +
				`ultimate_ok=${ultimate.filter((u) => u.fetch_status === "ok").length}`,
		);

		if (args.delayMs > 0) await sleep(args.delayMs);
	}

	console.error(
		`enrich done: processed=${processed} skipped=${done.size} errors=${errors} -> ${args.out}`,
	);
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});

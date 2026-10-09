// Shapes of what the studio API returns, for tsc --checkJs. Types only: nothing here ships to the browser.
// Post content (blocks, entities, media items) stays loosely typed on purpose: its fields come from the
// Bot API spec at runtime, and the editor must accept fields this file has never heard of.

type Json = Record<string, any>;

interface SpecField {
  name: string;
  types: string[];
  required: boolean;
  description: string;
  enum?: string[] | null;
}

interface SpecType {
  description: string;
  fields: SpecField[];
  subtypes: string[];
  const: string | null;
  href?: string | null;
}

interface SpecMethod {
  description: string;
  fields: SpecField[];
  href?: string | null;
}

/** GET /api/spec */
interface SpecSummary {
  version: string;
  release_date: string | null;
  changelog: string | null;
  source: string;
  loaded_at: number;
  types: Record<string, SpecType>;
  methods: Record<string, SpecMethod>;
  changes: Json[];
  gaps: SpecGap[];
  last_check: { at: number; error: string | null } | null;
}

/** What labels.js or render.js does not know yet (spec/coverage.py) */
interface SpecGap {
  part: 'labels' | 'preview';
  file: string;
  where: string;
  kind: 'block' | 'inline';
  name: string;
  type: string;
}

/** GET /api/updates, and `updates` in /api/config */
interface UpdatesStatus {
  enabled: boolean;
  state: 'starting' | 'ok' | 'off' | 'no_token' | 'conflict' | 'error';
  error: string | null;
  last_at: number | null;
  handled: number;
}

/** GET /api/config */
interface StudioConfig {
  default_chat?: string | null;
  preview_chat?: string | null;
  token_set?: boolean;
  api_base?: string;
  spec_version?: string;
  auth?: boolean;
  updates?: UpdatesStatus;
}

/** GET /api/drafts row */
interface DraftRow {
  id: string;
  title: string;
  updated_at: number;
  /** the post this draft edits, if any */
  post_id: string | null;
}

/** GET /api/posts/{id}/versions item */
interface PostVersion {
  id: number;
  payload: Json;
  replaced_at: number;
  actor: string | null;
}

/** GET /api/posts row */
interface PostRow {
  id: string;
  draft_id: string | null;
  chat_id: string;
  message_id: number | null;
  status: string;
  scheduled_at: number | null;
  sent_at: number | null;
  error: string | null;
  created_at: number;
  kind: string;
  actor: string | null;
  pinned: number | null;
  comment_status: string | null;
  reactions: Json | null;
  clicks: Json | null;
  poll_results: Json | null;
  poll_question: string | null;
}

/** GET /api/media item */
interface Upload {
  name: string;
  kind: string;
  url: string;
  ref: string;
  size: number;
  /** a draft or a live post mentions the file */
  used: boolean;
}

type ButtonRow = Json[];

/** POST /api/preview-send `parsed`: the post as Telegram's parser understood it; the editor adds `key` */
interface ParsedPost {
  /** rich_message the blocks belong to, as JSON: the post has not changed while it equals the current one */
  key: string;
  /** RichBlock* exactly as sendRichMessage returned them */
  blocks: Json[];
  /** the same blocks as InputRichBlock*, ready to send again; media reuse Telegram's file_id */
  input_blocks: Json[];
}

/** <rs-parse-check>: preview compared with Telegram's blocks; mine/theirs are outlines around the first difference */
interface ParseCheck {
  status: 'stale' | 'same' | 'diff';
  mine?: string;
  theirs?: string;
}

/** Editor state (state.js) */
interface EditorState {
  spec: SpecSummary;
  config: StudioConfig;
  draftId: string | null;
  /** version of the open draft as the server last confirmed it; a save based on another one gets 409 */
  draftVersion: number | null;
  /** a 409 dialog is open: autosave waits for the person's choice */
  conflict: boolean;
  format: 'blocks' | 'html' | 'markdown';
  mode: string;
  buf: { blocks: Json[]; html: string; markdown: string };
  media: Json[];
  flags: Json;
  keyboard: ButtonRow[];
  options: Json;
  extra: { pin: boolean; comment: string; commentObj: Json | null; callbacks: Record<string, string | Json> };
  updates: UpdatesStatus | null;
  uploads: Upload[];
  posts: PostRow[];
  drafts: DraftRow[];
}

/** one entry of GET /api/health `checks` */
interface HealthCheck {
  name: string;
  status: 'ok' | 'warn' | 'fail';
  summary: string;
  action?: string;
  details?: Json;
}

/** GET /api/health */
interface HealthReport {
  status: 'ok' | 'warn' | 'fail';
  version: string;
  bot_api: string;
  time: string;
  checked_at: number;
  problems: string[];
  checks: HealthCheck[];
  metrics: Json;
  recent_problems: Json[];
}

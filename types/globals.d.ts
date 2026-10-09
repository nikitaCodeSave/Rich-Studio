// Globals the classic scripts put on window for the editor modules.
interface RenderOptions {
  mediaBase?: string;
  channelTitle?: string;
  views?: string | number;
}
declare const RichRender: {
  render(post: object, opts?: RenderOptions): HTMLElement;
  renderRT(rt: unknown, ctx: object): Node;
  renderBlocks(blocks: unknown[], ctx: object): Node;
  formatTime(unix: number, fmt?: string): string;
  mediaSrc(media: unknown, ctx: object): string | null;
  safeUrl(url: string): string | null;
};
interface Window {
  RichRender: typeof RichRender;
  // vendored libraries the preview uses when present
  katex?: { render(expr: string, el: HTMLElement, opts?: object): void };
  markdownit?: (opts?: object) => { render(src: string): string };
  // snippets.js: snippet groups for the html and markdown modes
  SNIPPETS: Record<string, Record<string, any>[]>;
}

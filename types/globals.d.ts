// Globals the classic scripts put on window for the editor modules.
interface RenderOptions {
  mediaBase?: string;
  channelTitle?: string;
  views?: string | number;
}
declare const RichRender: { render(post: object, opts?: RenderOptions): HTMLElement };
interface Window {
  // snippets.js: snippet groups for the html and markdown modes
  SNIPPETS: Record<string, Record<string, any>[]>;
}

"""The telegram-rich-posts skill for agents that reach the studio only over MCP, as on a server.

The very file Claude Code reads locally (.claude/skills/telegram-rich-posts/SKILL.md), not a copy that would drift:
a resource under the skill:// convention of the MCP skills extension (SEP-2640) and a prompt of the same name,
which Claude Code offers as the command /rich-studio:telegram-rich-posts.
The file sits next to the app directory: in the repository that is the checkout, in the image the Dockerfile copies
it to the same place next to /app. The working directory does not matter.
"""

from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.resources import TextResource

NAME = "telegram-rich-posts"
URI = f"skill://{NAME}/SKILL.md"
FILE = Path(__file__).resolve().parent.parent.parent / ".claude" / "skills" / NAME / "SKILL.md"
TITLE = "Rich-посты в Telegram"


def text() -> str:
    # bytes, then decode: read_text would translate line endings, and the agent gets the file as it is
    return FILE.read_bytes().decode("utf-8")


def frontmatter(md: str) -> dict[str, str]:
    """The `key: value` lines between the leading `---` lines: name and description for the listings."""
    lines = md.split("\n")
    if lines[0] != "---" or "---" not in lines[1:]:
        return {}
    head = lines[1 : lines.index("---", 1)]
    return {k.strip(): v.strip() for k, sep, v in (line.partition(":") for line in head) if sep}


def register(server: MCPServer) -> None:
    """Read once, like the preview page: a studio built without the file does not start."""
    md = text()
    about = frontmatter(md).get("description")
    server.add_resource(
        TextResource(uri=URI, name=NAME, title=TITLE, description=about, mime_type="text/markdown", text=md)
    )

    @server.prompt(name=NAME, title=TITLE, description=about)
    def skill() -> str:
        return md

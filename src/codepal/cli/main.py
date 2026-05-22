"""Typer CLI entry point for CodePal."""

from __future__ import annotations

import typer

app = typer.Typer(
    name="codepal",
    help="CodePal — local AI coding assistant",
    add_completion=False,
)

hooks_app = typer.Typer(help="Git hook management")
app.add_typer(hooks_app, name="hooks")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8742, help="Bind port"),
    reload: bool = typer.Option(False, help="Enable auto-reload (dev mode)"),
    log_level: str = typer.Option("info", help="Log level"),
) -> None:
    """Start the CodePal FastAPI + MCP server."""
    import uvicorn

    from codepal.logging_config import configure_logging

    configure_logging(log_level)
    uvicorn.run(
        "codepal.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
    )


@app.command()
def index(
    path: str = typer.Argument(..., help="Absolute path to the project directory"),
) -> None:
    """Index a project directory for semantic search."""
    import asyncio

    import httpx

    async def _run() -> None:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8742", timeout=120) as client:
            resp = await client.post("/v1/index", json={"path": path})
            resp.raise_for_status()
            data = resp.json()
            typer.echo(f"Indexed {data['indexed']} chunks")
            if data.get("errors"):
                typer.echo(f"Errors: {data['errors']}", err=True)

    asyncio.run(_run())


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(5, help="Max results"),
) -> None:
    """Perform semantic search over the indexed codebase."""
    import asyncio

    import httpx

    async def _run() -> None:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8742", timeout=30) as client:
            resp = await client.get("/v1/search", params={"q": query, "limit": limit})
            resp.raise_for_status()
            data = resp.json()
            for r in data.get("results", []):
                typer.echo(
                    f"[{r['score']:.3f}] {r['file_path']} "
                    f"L{r['start_line']}-{r['end_line']} \u2014 {r['symbol_name']}"
                )
                typer.echo(f"  {r['text'][:120]}\n")

    asyncio.run(_run())


@hooks_app.command("install")
def hooks_install(
    project: str = typer.Option(..., help="Absolute path to the git project"),
    codepal_url: str = typer.Option(
        "http://127.0.0.1:8742", help="CodePal service URL"
    ),
) -> None:
    """Install the CodePal post-commit git hook into a project."""
    from codepal.hooks.installer import install_hook
    install_hook(project_path=project, codepal_url=codepal_url)


if __name__ == "__main__":
    app()

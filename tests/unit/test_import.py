def test_import_surface() -> None:
    """Given the package, when importing, then both public factories are exported."""
    import langgraph_runnable_server as p

    assert p.__all__ == ["create_app", "create_runnable_app"]
    from langgraph_runnable_server import create_app, create_runnable_app

    assert callable(create_app)
    assert callable(create_runnable_app)

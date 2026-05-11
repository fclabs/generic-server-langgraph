def test_import_surface() -> None:
    """Given the installed package, when importing, then only create_app is public."""
    import langgraph_runnable_server as p

    assert p.__all__ == ["create_app"]
    from langgraph_runnable_server import create_app

    assert callable(create_app)

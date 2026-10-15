def greet(name: str = "World") -> str:
    """Return a friendly greeting."""
    return f"Hello, {name}!"


def main() -> None:
    """Run the application entry point."""
    print(greet("DeepDive"))


if __name__ == "__main__":
    main()

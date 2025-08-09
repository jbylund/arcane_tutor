# Scryfall OS

An open source implementation of scryfall.
This project contains a few pieces:

1. The parsing library for the scryfall search DSL
1. Tooling for turning the parsed scryfall DSL into a database query
1. Tooling for loading a scryfall bulk data export into a postgres database (and hopefully for incrementally pulling in new cards, though the scale is probably sufficiently small that it doesn't matter)
1. a simple html + vanilla javascript web app which allows users to search for cards and have them displayed in an interface similar scryfall
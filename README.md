# VaeScroll: More than a slideshow, a sliding slideshow!

Basically it's a scrolling screensaver implemented in Python using PySide6.

### Why?

- Learning how to use AI in my personal projects and how it fits into IDE workflows
- Experimenting with Python. I'm mostly a C++ developer, but I'm interested in expanding my skillset and Python is extremely popular
- Creating a fun project I'll personally use
- Publishing something someone might want to use

# Working Features

- Scroll speed can be adjusted
- Assets can be loaded from directories dynamically
- Ability to extend the Tile class to include more complicated scenes

# Future Features

- Animations
- Layered tiles and composing
- Color hue cycling
- Text composing with the ability to fetch text from external sources
- HTML composing (this seems like a stretch)
- AI Image generation for automated tile generation
- Change scrolling direction
- Support for multiple monitors with independent scrolling
- Ability to disable scrolling and compose a static scene
  - Probably will need to implement a modularizeable composer

# Optimizations

- Investigate PySide6's built-in caching for images and other resources
- Loading and unloading of resources on a dedicated thread

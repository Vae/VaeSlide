# VaeScroll: More than a slideshow, a sliding slideshow!

Basically it's a scrolling screensaver implemented in Python using PySide6.

# Working Features

- Scroll speed can be adjusted
- Assets can be loaded from custom directories
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

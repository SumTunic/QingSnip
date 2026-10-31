"""All Rect values use physical pixels and exclusive right/bottom edges."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return max(0, self.w) * max(0, self.h)

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def intersect(self, other: "Rect") -> "Rect":
        x, y = max(self.x, other.x), max(self.y, other.y)
        return Rect(x, y, max(0, min(self.right, other.right) - x), max(0, min(self.bottom, other.bottom) - y))

    def translated(self, dx: int, dy: int) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    @classmethod
    def between(cls, start: tuple[int, int], end: tuple[int, int]) -> "Rect":
        return cls(min(start[0], end[0]), min(start[1], end[1]), abs(end[0] - start[0]), abs(end[1] - start[1]))


def candidates_at(rectangles: list[Rect], x: int, y: int, bounds: Rect) -> list[Rect]:
    unique = {r.intersect(bounds) for r in rectangles}
    return sorted((r for r in unique if r.w >= 24 and r.h >= 20 and r.contains(x, y)), key=lambda r: (r.area, r.x, r.y))

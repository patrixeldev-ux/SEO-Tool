import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DiffView } from "@/components/actions/diff-view";

describe("DiffView", () => {
  it("strikes out removed words on the left and highlights added words on the right", () => {
    const { container } = render(
      <DiffView type="rewrite_title" before="Buy cheap coffee beans online" after="Buy fresh coffee beans today" />,
    );
    expect([...container.querySelectorAll("del")].map((d) => d.textContent?.trim())).toEqual(["cheap", "online"]);
    expect([...container.querySelectorAll("ins")].map((d) => d.textContent?.trim())).toEqual(["fresh", "today"]);
    expect(screen.getByText("Now")).toBeInTheDocument();
    expect(screen.getByText("Proposed")).toBeInTheDocument();
  });

  it("shows unchanged text plainly when both sides are the same", () => {
    const { container } = render(<DiffView type="rewrite_title" before="Same title here" after="Same title here" />);
    expect(container.querySelectorAll("del, ins")).toHaveLength(0);
    expect(screen.getAllByText("Same title here")).toHaveLength(2);
  });

  it("says so when there is nothing yet, and when no suggestion exists", () => {
    const { rerender } = render(<DiffView type="add_alt" before={null} after="Dark roast beans in a bowl" />);
    expect(screen.getByText("(nothing there yet)")).toBeInTheDocument();
    rerender(<DiffView type="rewrite_title" before="Beans" after={null} />);
    expect(screen.getByText("(waiting for a suggestion)")).toBeInTheDocument();
    expect(screen.getByText("Beans")).toBeInTheDocument();
  });

  it("renders structured values as readable lines", () => {
    render(
      <DiffView
        type="add_internal_links"
        before={null}
        after={{ links: [{ to: "https://shop.example.com/grinders", anchor: "burr grinder" }] }}
      />,
    );
    expect(screen.getByText(/"burr grinder" → https:\/\/shop\.example\.com\/grinders/)).toBeInTheDocument();
  });
});

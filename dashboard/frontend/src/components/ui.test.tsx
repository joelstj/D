import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Badge, ToggleRow, Segmented } from "./ui";

describe("ui primitives", () => {
  it("renders a badge with its content", () => {
    render(<Badge tone="pos">+$100</Badge>);
    expect(screen.getByText("+$100")).toBeInTheDocument();
  });

  it("ToggleRow reflects state and fires onChange", async () => {
    const onChange = vi.fn();
    render(<ToggleRow label="Auto-execute" checked={false} onChange={onChange} />);
    const sw = screen.getByRole("switch", { name: "Auto-execute" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    await userEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("Segmented selects an option", async () => {
    const onChange = vi.fn();
    render(
      <Segmented
        value="paper"
        onChange={onChange}
        options={[
          { value: "paper", label: "Paper" },
          { value: "live", label: "Live" },
        ]}
      />,
    );
    await userEvent.click(screen.getByText("Live"));
    expect(onChange).toHaveBeenCalledWith("live");
  });
});

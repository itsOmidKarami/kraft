import { useNavigate } from "react-router-dom";
import { usePaneMemory } from "../Workspace";
import { placeUrl } from "../url";

/** Select a node on the item's chain with its pane open, or the chain itself (`null`): where a problem or a new node is shown. */
export function useSelect(itemId: string) {
  const navigate = useNavigate();
  const setPane = usePaneMemory((s) => s.setPane);
  return (node: string | null) => {
    setPane({ open: true, userCollapsed: false });
    navigate(placeUrl(itemId, { sel: node ? { kind: "node", node } : { kind: "chain" } }));
  };
}

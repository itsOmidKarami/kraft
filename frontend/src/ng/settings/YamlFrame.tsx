import { useState, type ReactNode } from "react";
import { Inspector } from "../graph/Inspector";
import { useResizable, useWidth } from "../graph/useResizable";
import { HeaderActions } from "../shell/HeaderActions";
import { Note } from "../templates/panes/controls";
import { Button } from "../ui/Button";
import "../templates/templates.css";
import "./settings.css";

/** A settings page with the header's YAML button and the pane it opens (AreaAccess,
 *  AreaAppearance): the file's text built from the page's own state, read-only.
 *  With `overview` the pane is always there, starting collapsed, on Overview and
 *  YAML tabs; without it the pane exists only while YAML is open. */
export function YamlFrame({ pageKey, file, title, icon, status = "saved on change", yaml, yamlNote, overview, children }: {
  pageKey: string;
  file: string;
  title: string;
  icon: string;
  status?: string;
  yaml: string;
  yamlNote?: string;
  overview?: ReactNode;
  children: ReactNode;
}) {
  const [frame, w] = useWidth();
  const size = useResizable(pageKey, w);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"overview" | "yaml">(overview ? "overview" : "yaml");
  const pressed = open && tab === "yaml";
  const toggle = () => {
    if (pressed && overview) return setTab("overview");
    if (pressed) return setOpen(false);
    setTab("yaml");
    setOpen(true);
  };
  const pane = !!overview || open;

  return (
    <div className="tpl-page" ref={frame}>
      <HeaderActions>
        <span className="saved-note">{status}</span>
        <Button aria-pressed={pressed} onClick={toggle}>YAML</Button>
      </HeaderActions>
      <div className="tpl-area">
        <div className="ys-body" style={{ right: size.overlay || !pane ? 0 : open ? size.width : 40 }}>{children}</div>
        {pane && (
          <Inspector
            id={`${pageKey}-pane`}
            open={open}
            size={size}
            crumbs={[]}
            icon={icon}
            title={title}
            sub={`${file} · ${status}`}
            tabs={[...(overview ? [{ value: "overview", label: "Overview" }] : []), { value: "yaml", label: "YAML" }]}
            tab={tab}
            onTab={(t) => setTab(t as "overview" | "yaml")}
            onCollapse={() => setOpen(false)}
            onExpand={() => setOpen(true)}
          >
            {tab === "yaml" ? (
              <>
                <pre className="ys-yaml" aria-label={file}>{yaml}</pre>
                {yamlNote && <Note>{yamlNote}</Note>}
              </>
            ) : overview}
          </Inspector>
        )}
      </div>
    </div>
  );
}

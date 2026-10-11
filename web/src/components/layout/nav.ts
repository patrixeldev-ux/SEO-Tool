import {
  ChartLine,
  ClipboardList,
  FileText,
  FlaskConical,
  Globe,
  ListChecks,
  Settings,
  Sparkles,
  Zap,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
}

export interface NavSection {
  title: string;
  items: NavItem[];
}

export const NAV_SECTIONS: NavSection[] = [
  { title: "Workspace", items: [{ to: "/sites", label: "Sites", icon: Globe }] },
  {
    title: "Observe",
    items: [
      { to: "/audit", label: "Audit", icon: ListChecks },
      { to: "/search-console", label: "Search Console", icon: ChartLine },
      { to: "/ai-visibility", label: "AI Visibility", icon: Sparkles },
    ],
  },
  {
    title: "Act",
    items: [
      { to: "/actions", label: "Actions", icon: Zap },
      { to: "/experiments", label: "Experiments", icon: FlaskConical },
      { to: "/content", label: "Content", icon: FileText },
    ],
  },
  { title: "Report", items: [{ to: "/reports", label: "Reports", icon: ClipboardList }] },
];

export const SETTINGS_ITEM: NavItem = { to: "/settings", label: "Settings", icon: Settings };

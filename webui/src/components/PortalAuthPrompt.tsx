import { useTranslation } from "react-i18next";
import type { PortalContext } from "@/lib/portal-context";

export function PortalAuthPrompt({ portal }: { portal: PortalContext }) {
  const { t } = useTranslation();
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-6 text-foreground">
      <section className="w-full max-w-sm space-y-4" aria-labelledby="portal-auth-title">
        <h1 id="portal-auth-title" className="text-xl font-semibold">{t("app.portal.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("app.portal.description")}</p>
        <a className="inline-flex rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground" href={portal.portalUrl}>
          {t("app.portal.open")}
        </a>
      </section>
    </main>
  );
}

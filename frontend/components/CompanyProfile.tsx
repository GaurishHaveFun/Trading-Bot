import type { TickerProfile } from "@/lib/types";

/**
 * Company profile panel: business summary (clamped, expandable) plus a
 * small definition list. Uses <details>/<summary> instead of useState so
 * this stays a server component. Returns null when there's nothing to show
 * at all — an all-null profile shouldn't render an empty glass panel.
 */
export default function CompanyProfile({ profile }: { profile: TickerProfile }) {
  const hasAnyField =
    profile.business_summary ||
    profile.sector ||
    profile.industry ||
    profile.employees !== null ||
    profile.country ||
    profile.website;

  if (!hasAnyField) return null;

  return (
    <div className="glass-panel panel-enter p-4">
      <h2 className="mb-3 text-sm font-medium text-foreground-muted">Company profile</h2>

      {profile.business_summary && (
        <details className="mb-4 group">
          <summary className="cursor-pointer list-none">
            <p className="line-clamp-4 text-sm leading-relaxed text-foreground-muted group-open:line-clamp-none">
              {profile.business_summary}
            </p>
            <span className="mt-1 inline-block text-xs text-gradient-accent">
              Show more
            </span>
          </summary>
        </details>
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs uppercase tracking-wide text-foreground-muted">Sector</dt>
          <dd className="mt-0.5 text-foreground">{profile.sector ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-foreground-muted">Industry</dt>
          <dd className="mt-0.5 text-foreground">{profile.industry ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-foreground-muted">Employees</dt>
          <dd className="mt-0.5 text-foreground">
            {profile.employees !== null && profile.employees !== undefined
              ? profile.employees.toLocaleString()
              : "—"}
          </dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-foreground-muted">Country</dt>
          <dd className="mt-0.5 text-foreground">{profile.country ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-foreground-muted">Website</dt>
          <dd className="mt-0.5 text-foreground">
            {profile.website ? (
              <a
                href={profile.website}
                target="_blank"
                rel="noreferrer"
                className="text-gradient-accent hover:underline"
              >
                {profile.website.replace(/^https?:\/\//, "")}
              </a>
            ) : (
              "—"
            )}
          </dd>
        </div>
      </dl>
    </div>
  );
}

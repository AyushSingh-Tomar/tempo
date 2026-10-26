import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { DiscrepancyFinding, ProcessingRun } from '../../api/types';
import { ErrorState } from '../../components/common/ErrorState';
import { LoadingState } from '../../components/common/LoadingState';
import { DiscrepancySummary } from '../../components/discrepancies/DiscrepancySummary';
import { DiscrepancyTable } from '../../components/discrepancies/DiscrepancyTable';
import { DiscrepancyDetailDrawer } from '../../components/discrepancies/DiscrepancyDetailDrawer';
import { OverrideModal } from '../../components/discrepancies/OverrideModal';
import { api } from '../../api/endpoints';
import { describeError, isNotAvailable } from '../../api/errors';
import { discrepanciesKey, useDiscrepancies } from '../../hooks/useDiscrepancies';
import { useRunAction } from '../../hooks/useRunAction';
import { parseDevToken, useAuthToken } from '../../stores/devAuth';

const OVERRIDE_ROLES = new Set(['reviewer', 'admin', 'project_owner']);

export function DiscrepancyStep({ run }: { run: ProcessingRun }) {
  const queryClient = useQueryClient();
  const token = useAuthToken();
  const role = parseDevToken(token)?.role ?? null;
  const canOverride = !!role && OVERRIDE_ROLES.has(role);

  const { data, isLoading, error, refetch } = useDiscrepancies(run.id, true);
  const [selected, setSelected] = useState<DiscrepancyFinding | null>(null);
  const [overriding, setOverriding] = useState(false);

  const override = useMutation({
    mutationFn: ({ id, reason, comment }: { id: string; reason: string; comment?: string }) =>
      api.discrepancies.override(id, { reason, comment }),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: discrepanciesKey(run.id) }),
  });

  const proceed = useRunAction(run.id, () => api.discrepancies.proceed(run.id));

  if (isLoading) return <LoadingState label="Loading discrepancy results..." />;
  if (error) {
    if (isNotAvailable(error) && run.status === 'FINANCIAL_CHECKS_COMPLETED') {
      return <LoadingState label="Financial checks completed. Preparing discrepancy report..." />;
    }
    return <ErrorState error={error} title="Could not load discrepancies" onRetry={() => refetch()} />;
  }
  if (!data) return <LoadingState label="Loading discrepancy results..." />;

  const findings = data.findings;
  const canProceed = run.status === 'DISCREPANCY_REVIEW_PENDING';

  return (
    <div className="space-y-6">
      <DiscrepancySummary findings={findings} />

      {data.columnRoleSuggestions.length > 0 && (
        <section className="rounded border border-slate-200 bg-slate-50 p-3 text-sm" aria-labelledby="role-suggestions-heading">
          <h3 id="role-suggestions-heading" className="font-semibold text-slate-800">
            Relationship check on unmapped numeric columns
          </h3>
          <p className="mt-1 text-slate-600">
            These columns matched no target field. Tested against the premium relationships using your treaty figures,
            they behave like the fields below. Suggestions only - nothing has been re-mapped.
          </p>
          <ul className="mt-2 list-disc pl-5">
            {data.columnRoleSuggestions.map((s) => (
              <li key={s.source_column}>
                <span className="font-medium">{s.source_column}</span>
                {s.suggested_field ? ` looks like ${s.suggested_field}` : ' fits more than one role (ambiguous)'}
                {s.matched_rules.length > 0 && <span className="text-slate-500"> ({s.matched_rules.join(', ')})</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {findings.length > 0 && <DiscrepancyTable findings={findings} onSelect={(f) => setSelected(f)} />}

      {proceed.isError && <ErrorState error={proceed.error} title="Could not proceed" />}

      <button
        type="button"
        onClick={() => proceed.mutate(undefined)}
        disabled={!canProceed || proceed.isPending}
        className="rounded bg-slate-900 px-4 py-2 font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-700"
      >
        {proceed.isPending ? 'Proceeding...' : 'Proceed to report generation'}
      </button>
      {!canProceed && (
        <p className="text-xs text-slate-500">You can inspect findings now; proceeding becomes available once results are finalized.</p>
      )}

      {selected && (
        <DiscrepancyDetailDrawer
          finding={selected}
          onClose={() => setSelected(null)}
          canOverride={canOverride && !selected.override}
          onOverride={() => setOverriding(true)}
        />
      )}

      {selected && overriding && (
        <OverrideModal
          finding={selected}
          submitting={override.isPending}
          error={override.isError ? describeError(override.error) : null}
          onClose={() => setOverriding(false)}
          onSubmit={(reason, comment) =>
            override.mutate(
              { id: selected.id, reason, comment: comment || undefined },
              { onSuccess: () => setOverriding(false) },
            )
          }
        />
      )}
    </div>
  );
}

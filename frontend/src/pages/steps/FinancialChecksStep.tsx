import type { ProcessingRun } from '../../api/types';
import { ErrorState } from '../../components/common/ErrorState';
import { useRunAction } from '../../hooks/useRunAction';
import { api } from '../../api/endpoints';

export function FinancialChecksStep({ run }: { run: ProcessingRun }) {
  const run_checks = useRunAction(run.id, () => api.financialChecks.run(run.id));

  return (
    <div className="space-y-4">
      <p className="text-sm text-green-700">Treaty figures validated.</p>
      <p className="text-sm text-slate-600">
        Apply the configured money-column rules against the standardized data and the submitted treaty figures.
      </p>
      {run_checks.isError && <ErrorState error={run_checks.error} title="Financial checks failed to run" />}
      <button
        type="button"
        onClick={() => run_checks.mutate(undefined)}
        disabled={run_checks.isPending}
        className="rounded bg-slate-900 px-4 py-2 font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-700"
      >
        {run_checks.isPending ? 'Running financial checks...' : 'Run financial checks'}
      </button>
    </div>
  );
}

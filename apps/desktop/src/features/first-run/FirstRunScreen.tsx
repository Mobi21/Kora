import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { WizardLayout, type WizardStep } from './components/WizardLayout';
import { StepWelcome } from './components/StepWelcome';
import { StepDaemonCheck } from './components/StepDaemonCheck';
import { StepTokenMemory } from './components/StepTokenMemory';
import { markFirstRunCompleted } from './queries';

const STEPS: readonly WizardStep[] = [
  { index: 1, label: 'Welcome' },
  { index: 2, label: 'Daemon check' },
  { index: 3, label: 'Token & memory' },
];

interface StepConfig {
  title: string;
  subtitle: string;
}

const STEP_CONFIG: Record<number, StepConfig> = {
  0: {
    title: 'Welcome.',
    subtitle:
      'Three short steps to make sure Kora can talk to its local daemon and find its memory store.',
  },
  1: {
    title: 'Find the daemon.',
    subtitle:
      'Kora is a Python service running on your machine. We need to confirm it\u2019s reachable before we go further.',
  },
  2: {
    title: 'Token & memory.',
    subtitle:
      'Pick where Kora\u2019s memory lives. Your data stays in this folder; nothing else moves.',
  },
};

export function FirstRunScreen(): JSX.Element {
  const navigate = useNavigate();
  const [stepIndex, setStepIndex] = useState(0);

  const finish = useCallback(() => {
    markFirstRunCompleted();
    navigate('/today', { replace: true });
  }, [navigate]);

  const config = STEP_CONFIG[stepIndex] ?? STEP_CONFIG[0];

  return (
    <WizardLayout
      steps={STEPS}
      currentIndex={stepIndex}
      title={config.title}
      subtitle={config.subtitle}
    >
      {stepIndex === 0 && <StepWelcome onContinue={() => setStepIndex(1)} />}
      {stepIndex === 1 && (
        <StepDaemonCheck
          onAdvance={() => setStepIndex(2)}
          onSkip={() => setStepIndex(2)}
        />
      )}
      {stepIndex === 2 && (
        <StepTokenMemory
          onFinish={(_memoryRoot) => {
            // The memory root is informational at this stage — the actual
            // setting lives in the daemon's config and is exposed through
            // Settings → Memory. The wizard's job is to confirm the user
            // knows where memory will live; persisting the chosen path is
            // a follow-up once a settings RPC is wired.
            finish();
          }}
        />
      )}
    </WizardLayout>
  );
}

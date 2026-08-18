import { useEffect, useRef, useState } from 'react'
import PresetEditor from '@/components/encoder/PresetEditor'
import EncoderSelect from '@/components/encoder/EncoderSelect'
import DirectorySelect from '@/components/ui/DirectorySelect'
import {
  fetchEncoderDirectories,
  fetchEncoderFiles,
  reprocessEncoderFile,
  saveEncoderConfig,
  saveEncoderRules,
  testEncoderFile,
} from '@/lib/api'
import type {
  EncoderConfig,
  EncoderDirectory,
  EncoderFile,
  EncoderHealth,
  EncoderPreset,
  EncoderReprocessEvent,
  EncoderReprocessRun,
  EncoderRule,
  EncoderRuleCondition,
  EncoderTestResult,
} from '@/types'

type RuleSet = {
  rules: EncoderRule[]
  fallback: string
}

export interface EncoderSettingsProps {
  config: EncoderConfig
  health?: EncoderHealth | null
  presets: EncoderPreset[]
  rules: RuleSet
  onRefresh: () => void
  onError: (message: string) => void
  onStartReprocessAll?: () => Promise<EncoderReprocessRun>
  latestReprocessEvent?: EncoderReprocessEvent | null
  reprocessActive?: boolean | null
}

type Section = 'watch' | 'presets' | 'rules'

const FIELDS = [
  'height',
  'width',
  'size',
  'bit_rate',
  'bit_depth',
  'frame_rate',
  'duration',
  'video_codec',
  'profile',
  'source_tool',
  'encoder_tag',
  'hdr',
  'dolby_vision',
]
const OPERATORS = ['>=', '<=', '>', '<', '==', '!=', 'contains']
const BOOLEAN_FIELDS = new Set(['hdr', 'dolby_vision'])
const NUMERIC_FIELDS = new Set([
  'height',
  'width',
  'size',
  'bit_rate',
  'bit_depth',
  'frame_rate',
  'duration',
])
const SOURCE_TOOLS = ['unknown', 'makemkv', 'handbrake', 'lavf', 'other']

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function cloneRules(rules: EncoderRule[]): EncoderRule[] {
  return rules.map((rule) => ({
    ...rule,
    conditions: rule.conditions.map((condition) => ({ ...condition })),
  }))
}

function parseConditionValue(field: string, value: string): unknown {
  if (BOOLEAN_FIELDS.has(field)) return value === 'true'
  if (NUMERIC_FIELDS.has(field)) {
    const parsed = Number(value)
    return value.trim() !== '' && Number.isFinite(parsed) ? parsed : value
  }
  return value
}

function initialCondition(): EncoderRuleCondition {
  return { field: 'height', op: '>=', value: 2160 }
}

function operatorsFor(field: string): string[] {
  if (BOOLEAN_FIELDS.has(field)) return ['==', '!=']
  if (NUMERIC_FIELDS.has(field)) return OPERATORS.filter((operator) => operator !== 'contains')
  return OPERATORS
}

function nextRuleId(rules: EncoderRule[]): string {
  const used = new Set(rules.map((rule) => rule.id.trim()))
  let suffix = 1
  while (used.has(`rule-${suffix}`)) suffix += 1
  return `rule-${suffix}`
}

function SectionButton({
  label,
  section,
  open,
  onToggle,
}: {
  label: string
  section: Section
  open: boolean
  onToggle: (section: Section) => void
}) {
  return (
    <button
      type="button"
      aria-expanded={open}
      onClick={() => onToggle(section)}
      className={`rounded-xl border px-3 py-2 text-[0.78rem] font-medium transition ${
        open
          ? 'border-teal-400/30 bg-teal-400/15 text-teal-300'
          : 'border-white/8 bg-white/4 text-[var(--text-secondary)] hover:border-white/15 hover:text-white'
      }`}
    >
      {label}
    </button>
  )
}

export default function EncoderSettings({
  config,
  health,
  presets,
  rules,
  onRefresh,
  onError,
  onStartReprocessAll,
  latestReprocessEvent = null,
  reprocessActive = null,
}: EncoderSettingsProps) {
  const [openSection, setOpenSection] = useState<Section | null>(null)
  const [watchPaths, setWatchPaths] = useState(() => [...config.watch_paths])
  const [watchDirty, setWatchDirty] = useState(false)
  const [draftRules, setDraftRules] = useState(() => cloneRules(rules.rules))
  const [fallback, setFallback] = useState(rules.fallback)
  const [rulesDirty, setRulesDirty] = useState(false)
  const [savingPaths, setSavingPaths] = useState(false)
  const [savingRules, setSavingRules] = useState(false)
  const [testPath, setTestPath] = useState('')
  const [testResult, setTestResult] = useState<EncoderTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [reprocessing, setReprocessing] = useState(false)
  const [reprocessMessage, setReprocessMessage] = useState<string | null>(null)
  const [confirmReprocessAll, setConfirmReprocessAll] = useState(false)
  const [startingReprocessAll, setStartingReprocessAll] = useState(false)
  const [activeReprocessRunId, setActiveReprocessRunId] = useState<string | null>(null)
  const testPathRef = useRef('')
  const testRequestRef = useRef(0)
  const reprocessRequestRef = useRef(0)
  const [directorySearch, setDirectorySearch] = useState('')
  const [directories, setDirectories] = useState<EncoderDirectory[]>([])
  const [directoriesLoading, setDirectoriesLoading] = useState(false)
  const [fileDirectory, setFileDirectory] = useState('')
  const [fileSearch, setFileSearch] = useState('')
  const [files, setFiles] = useState<EncoderFile[]>([])
  const [filesLoading, setFilesLoading] = useState(false)
  const pendingWatchPaths = useRef<string[] | null>(null)
  const pendingRules = useRef<RuleSet | null>(null)
  const activeReprocessRunIdRef = useRef<string | null>(null)
  const latestReprocessEventRef = useRef(latestReprocessEvent)
  latestReprocessEventRef.current = latestReprocessEvent

  const samePaths = (left: string[], right: string[]) =>
    left.length === right.length && left.every((path, index) => path === right[index])
  const sameRuleSet = (left: RuleSet, right: RuleSet) =>
    JSON.stringify(left) === JSON.stringify(right)

  useEffect(() => {
    const pending = pendingWatchPaths.current
    if (pending && samePaths(config.watch_paths, pending)) {
      pendingWatchPaths.current = null
    }
    if (!watchDirty && !pendingWatchPaths.current) setWatchPaths([...config.watch_paths])
  }, [config.watch_paths, watchDirty])
  useEffect(() => {
    const pending = pendingRules.current
    const incoming = { rules: rules.rules, fallback: rules.fallback }
    if (pending && sameRuleSet(incoming, pending)) {
      pendingRules.current = null
    }
    if (!rulesDirty && !pendingRules.current) {
      setDraftRules(cloneRules(rules.rules))
      setFallback(rules.fallback)
    }
  }, [rules, rulesDirty])

  useEffect(() => {
    if (openSection !== 'watch' && openSection !== 'rules') return
    if (typeof fetchEncoderDirectories !== 'function') return
    let cancelled = false
    setDirectoriesLoading(true)
    const timer = window.setTimeout(() => {
      void fetchEncoderDirectories(directorySearch)
        .then((result) => {
          if (!cancelled) {
            setDirectories(result.directories)
            if (openSection === 'rules') {
              setFileDirectory((current) => current || result.directories[0]?.path || '')
            }
          }
        })
        .catch((error) => {
          if (!cancelled) onError(errorMessage(error))
        })
        .finally(() => {
          if (!cancelled) setDirectoriesLoading(false)
        })
    }, 180)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [directorySearch, onError, openSection])

  useEffect(() => {
    if (openSection !== 'rules') return
    if (!fileDirectory) {
      setFiles([])
      setFilesLoading(false)
      return
    }
    if (typeof fetchEncoderFiles !== 'function') return
    let cancelled = false
    const timer = window.setTimeout(() => {
      setFilesLoading(true)
      void fetchEncoderFiles(fileDirectory, fileSearch)
        .then((result) => {
          if (!cancelled) {
            setFiles(result.files)
            if (!testPathRef.current && result.files[0]) changeTestPath(result.files[0].path)
          }
        })
        .catch((error) => {
          if (!cancelled) onError(errorMessage(error))
        })
        .finally(() => {
          if (!cancelled) setFilesLoading(false)
        })
    }, 180)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [fileDirectory, fileSearch, onError])

  const toggle = (section: Section) => {
    setOpenSection((current) => (current === section ? null : section))
  }

  const savePaths = async () => {
    const paths = watchPaths.map((path) => path.trim()).filter(Boolean)
    setSavingPaths(true)
    try {
      await saveEncoderConfig(paths)
      setWatchPaths(paths)
      setWatchDirty(false)
      pendingWatchPaths.current = paths
      onRefresh()
    } catch (error) {
      onError(errorMessage(error))
    } finally {
      setSavingPaths(false)
    }
  }

  const updateRule = (index: number, patch: Partial<EncoderRule>) => {
    pendingRules.current = null
    setRulesDirty(true)
    setDraftRules((current) =>
      current.map((rule, ruleIndex) => (ruleIndex === index ? { ...rule, ...patch } : rule)),
    )
  }

  const updateCondition = (
    ruleIndex: number,
    conditionIndex: number,
    patch: Partial<EncoderRuleCondition>,
  ) => {
    pendingRules.current = null
    setRulesDirty(true)
    setDraftRules((current) =>
      current.map((rule, index) =>
        index === ruleIndex
          ? {
              ...rule,
              conditions: rule.conditions.map((condition, itemIndex) =>
                itemIndex === conditionIndex ? { ...condition, ...patch } : condition,
              ),
            }
          : rule,
      ),
    )
  }

  const moveRule = (index: number, offset: -1 | 1) => {
    pendingRules.current = null
    setRulesDirty(true)
    setDraftRules((current) => {
      const destination = index + offset
      if (destination < 0 || destination >= current.length) return current
      const next = [...current]
      ;[next[index], next[destination]] = [next[destination]!, next[index]!]
      return next
    })
  }

  const saveRules = async () => {
    const normalizedRules = draftRules.map((rule) => ({ ...rule, id: rule.id.trim() }))
    const ids = normalizedRules.map((rule) => rule.id)
    if (ids.some((id) => !id)) {
      onError('Rule IDs cannot be empty.')
      return
    }
    if (new Set(ids).size !== ids.length) {
      onError('Rule IDs must be unique.')
      return
    }

    setSavingRules(true)
    try {
      await saveEncoderRules({ rules: normalizedRules, fallback })
      pendingRules.current = { rules: cloneRules(normalizedRules), fallback }
      setRulesDirty(false)
      onRefresh()
    } catch (error) {
      onError(errorMessage(error))
    } finally {
      setSavingRules(false)
    }
  }

  const runTest = async () => {
    const path = testPath.trim()
    if (!path) return
    const request = ++testRequestRef.current
    setTesting(true)
    setTestResult(null)
    setReprocessMessage(null)
    try {
      const result = await testEncoderFile(path)
      if (request === testRequestRef.current && testPathRef.current.trim() === path) {
        setTestResult(result)
      }
    } catch (error) {
      if (request === testRequestRef.current && testPathRef.current.trim() === path) {
        onError(errorMessage(error))
      }
    } finally {
      if (request === testRequestRef.current) setTesting(false)
    }
  }

  const reprocess = async () => {
    const path = testPath.trim()
    if (!path) return
    const request = ++reprocessRequestRef.current
    setReprocessing(true)
    setReprocessMessage(null)
    try {
      const result = await reprocessEncoderFile(path)
      if (request === reprocessRequestRef.current && testPathRef.current.trim() === path) {
        setReprocessMessage(
          result.created
            ? 'File was re-evaluated immediately.'
            : 'An active job already exists for this file.',
        )
      }
    } catch (error) {
      if (request === reprocessRequestRef.current && testPathRef.current.trim() === path) {
        onError(errorMessage(error))
      }
    } finally {
      if (request === reprocessRequestRef.current) setReprocessing(false)
    }
  }

  const changeTestPath = (path: string) => {
    testPathRef.current = path
    testRequestRef.current += 1
    reprocessRequestRef.current += 1
    setTestPath(path)
    setTestResult(null)
    setReprocessMessage(null)
    setTesting(false)
    setReprocessing(false)
  }

  const hasActiveReprocessEvent =
    latestReprocessEvent?.status === 'started' || latestReprocessEvent?.status === 'running'
  const reprocessAllActive =
    startingReprocessAll || activeReprocessRunId !== null || hasActiveReprocessEvent

  useEffect(() => {
    if (reprocessActive === false) {
      activeReprocessRunIdRef.current = null
      setActiveReprocessRunId(null)
      return
    }
    if (
      !latestReprocessEvent ||
      latestReprocessEvent.run_id !== activeReprocessRunId ||
      (latestReprocessEvent.status !== 'completed' && latestReprocessEvent.status !== 'failed')
    ) {
      return
    }
    activeReprocessRunIdRef.current = null
    setActiveReprocessRunId(null)
  }, [activeReprocessRunId, latestReprocessEvent, reprocessActive])

  const startReprocessAll = async () => {
    if (!onStartReprocessAll || reprocessAllActive || activeReprocessRunIdRef.current) return
    setStartingReprocessAll(true)
    try {
      const run = await onStartReprocessAll()
      const latest = latestReprocessEventRef.current
      if (
        latest?.run_id === run.run_id &&
        (latest.status === 'completed' || latest.status === 'failed')
      ) {
        activeReprocessRunIdRef.current = null
        setActiveReprocessRunId(null)
      } else {
        activeReprocessRunIdRef.current = run.run_id
        setActiveReprocessRunId(run.run_id)
      }
      setConfirmReprocessAll(false)
    } catch (error) {
      onError(errorMessage(error))
    } finally {
      setStartingReprocessAll(false)
    }
  }

  const reprocessAllMessage = latestReprocessEvent
    ? `Bulk re-evaluation ${latestReprocessEvent.status} — ${latestReprocessEvent.scanned} scanned, ${latestReprocessEvent.created} ${config.mode === 'review' ? 'awaiting review' : 'queued'}, ${latestReprocessEvent.skipped} skipped, ${latestReprocessEvent.failed} failed${latestReprocessEvent.error ? `: ${latestReprocessEvent.error}` : ''}`
    : null

  const selectWatchPath = (index: number, path: string) => {
    pendingWatchPaths.current = null
    setWatchDirty(true)
    setWatchPaths((current) =>
      current.map((item, itemIndex) => (itemIndex === index ? path : item)),
    )
  }

  const selectedDirectory = (path: string): EncoderDirectory =>
    directories.find((directory) => directory.path === path) ?? { path, base: '' }

  const targets = Array.from(
    new Set([
      'skip',
      ...presets.map((preset) => preset.name),
      ...draftRules.map((rule) => rule.target),
      fallback,
    ]),
  )
  const watchDirectoryOptions = Array.from(
    new Map(
      [...directories, ...watchPaths.filter(Boolean).map((path) => selectedDirectory(path))].map(
        (directory) => [directory.path, directory],
      ),
    ).values(),
  )

  return (
    <section aria-label="Encoder settings" className="glass rounded-[16px] p-4">
      <div className="flex flex-wrap gap-2">
        <SectionButton
          label="Watch Folders"
          section="watch"
          open={openSection === 'watch'}
          onToggle={toggle}
        />
        <SectionButton
          label="Presets"
          section="presets"
          open={openSection === 'presets'}
          onToggle={toggle}
        />
        <SectionButton
          label="Rules"
          section="rules"
          open={openSection === 'rules'}
          onToggle={toggle}
        />
      </div>

      {openSection === 'watch' && (
        <div className="mt-4 space-y-3 rounded-xl border border-white/8 bg-white/3 p-4">
          <label className="block text-[0.72rem] font-medium text-[var(--text-secondary)]">
            Search available folders
            <input
              value={directorySearch}
              onChange={(event) => setDirectorySearch(event.target.value)}
              placeholder="Filter directories…"
              className="input-field input-teal mt-1"
            />
          </label>
          {watchPaths.length === 0 && (
            <p className="text-[0.76rem] text-[var(--text-tertiary)]">
              Watching is paused until a folder is added and saved.
            </p>
          )}
          {watchPaths.map((path, index) => (
            <div key={`${path}-${index}`} className="flex items-end gap-2">
              <div className="min-w-0 flex-1">
                <DirectorySelect
                  directories={watchDirectoryOptions}
                  value={path}
                  base={selectedDirectory(path).base}
                  onChange={(selected) => selectWatchPath(index, selected)}
                  onRefresh={() => setDirectorySearch('')}
                  isLoading={directoriesLoading}
                  color="teal"
                  showBaseLabel
                  onClear={() => selectWatchPath(index, '')}
                />
                <input
                  aria-label={`Watch folder ${index + 1}`}
                  value={path}
                  onChange={(event) => selectWatchPath(index, event.target.value)}
                  className="sr-only"
                  tabIndex={-1}
                />
              </div>
              <button
                type="button"
                aria-label={`Remove watch folder ${index + 1}`}
                onClick={() => {
                  pendingWatchPaths.current = null
                  setWatchDirty(true)
                  setWatchPaths((current) => current.filter((_, itemIndex) => itemIndex !== index))
                }}
                className="rounded-lg border border-red-400/20 bg-red-400/8 px-3 py-2 text-[0.75rem] text-red-300 transition hover:bg-red-400/15"
              >
                Remove
              </button>
            </div>
          ))}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                pendingWatchPaths.current = null
                setWatchDirty(true)
                setWatchPaths((current) => [...current, ''])
              }}
              className="rounded-lg border border-teal-400/25 bg-teal-400/10 px-3 py-2 text-[0.75rem] font-medium text-teal-300 transition hover:bg-teal-400/20"
            >
              Add watch folder
            </button>
            <button
              type="button"
              disabled={savingPaths}
              onClick={() => void savePaths()}
              className="rounded-lg border border-teal-500/30 bg-teal-500/15 px-3 py-2 text-[0.75rem] font-medium text-teal-300 transition hover:bg-teal-500/25 disabled:opacity-50"
            >
              {savingPaths ? 'Saving…' : 'Save watch folders'}
            </button>
          </div>
        </div>
      )}

      {openSection === 'presets' && (
        <div className="mt-4 border-t border-white/6 pt-4">
          <PresetEditor
            presets={presets}
            health={health}
            onSaved={onRefresh}
            onDeleted={onRefresh}
            onError={onError}
          />
        </div>
      )}

      {openSection === 'rules' && (
        <div className="mt-4 space-y-4 border-t border-white/6 pt-4">
          <ol aria-label="Encoding rules" className="space-y-3">
            {draftRules.map((rule, ruleIndex) => (
              <li key={ruleIndex} className="rounded-xl border border-white/8 bg-white/3 p-3">
                <div className="flex flex-wrap items-end gap-2">
                  <label className="min-w-[10rem] flex-1 text-[0.7rem] text-[var(--text-secondary)]">
                    Rule name
                    <input
                      aria-label={`Rule ${ruleIndex + 1} name`}
                      value={rule.id}
                      onChange={(event) => updateRule(ruleIndex, { id: event.target.value })}
                      className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-2.5 py-2 text-[0.78rem] text-[var(--text-primary)]"
                    />
                  </label>
                  <button
                    type="button"
                    disabled={ruleIndex === 0}
                    aria-label={`Move rule ${rule.id} up`}
                    onClick={() => moveRule(ruleIndex, -1)}
                    className="rounded-lg border border-white/8 bg-white/4 px-2.5 py-1.5 text-[0.75rem] text-[var(--text-secondary)] transition hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    disabled={ruleIndex === draftRules.length - 1}
                    aria-label={`Move rule ${rule.id} down`}
                    onClick={() => moveRule(ruleIndex, 1)}
                    className="rounded-lg border border-white/8 bg-white/4 px-2.5 py-1.5 text-[0.75rem] text-[var(--text-secondary)] transition hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    aria-label={`Remove rule ${rule.id}`}
                    onClick={() => (
                      (pendingRules.current = null),
                      setRulesDirty(true),
                      setDraftRules((current) =>
                        current.filter((_, itemIndex) => itemIndex !== ruleIndex),
                      )
                    )}
                    className="rounded-lg border border-red-400/20 bg-red-400/8 px-2.5 py-1.5 text-[0.72rem] text-red-300 transition hover:bg-red-400/15"
                  >
                    Remove
                  </button>
                </div>

                <div className="mt-3 space-y-2">
                  {rule.conditions.map((condition, conditionIndex) => (
                    <div key={conditionIndex}>
                      {conditionIndex > 0 && (
                        <p className="mb-2 text-[0.62rem] font-semibold tracking-[0.12em] text-teal-300">
                          AND
                        </p>
                      )}
                      <div className="grid gap-2 sm:grid-cols-[1fr_5rem_1fr_auto]">
                        <label className="field-label text-[0.7rem] text-[var(--text-secondary)]">
                          Field
                          <EncoderSelect
                            aria-label={`${rule.id} condition ${conditionIndex + 1} field`}
                            value={condition.field}
                            onChange={(event) => {
                              const field = event.target.value
                              updateCondition(ruleIndex, conditionIndex, {
                                field,
                                op:
                                  BOOLEAN_FIELDS.has(field) ||
                                  (NUMERIC_FIELDS.has(field) && condition.op === 'contains')
                                    ? '=='
                                    : condition.op,
                                value: BOOLEAN_FIELDS.has(field)
                                  ? true
                                  : NUMERIC_FIELDS.has(field)
                                    ? 0
                                    : '',
                              })
                            }}
                            className="mt-1"
                            options={FIELDS}
                          />
                        </label>
                        <label className="field-label text-[0.7rem] text-[var(--text-secondary)]">
                          Operator
                          <EncoderSelect
                            aria-label={`${rule.id} condition ${conditionIndex + 1} operator`}
                            value={condition.op}
                            onChange={(event) =>
                              updateCondition(ruleIndex, conditionIndex, { op: event.target.value })
                            }
                            className="mt-1"
                            options={operatorsFor(condition.field)}
                          />
                        </label>
                        <label className="field-label text-[0.7rem] text-[var(--text-secondary)]">
                          Value
                          {BOOLEAN_FIELDS.has(condition.field) ? (
                            <EncoderSelect
                              aria-label={`${rule.id} condition ${conditionIndex + 1} value`}
                              value={String(condition.value)}
                              onChange={(event) =>
                                updateCondition(ruleIndex, conditionIndex, {
                                  value: event.target.value === 'true',
                                })
                              }
                              className="mt-1"
                              options={['true', 'false']}
                            />
                          ) : condition.field === 'source_tool' ? (
                            <EncoderSelect
                              aria-label={`${rule.id} condition ${conditionIndex + 1} value`}
                              value={String(condition.value)}
                              onChange={(event) =>
                                updateCondition(ruleIndex, conditionIndex, {
                                  value: event.target.value,
                                })
                              }
                              className="mt-1"
                              options={SOURCE_TOOLS.map((tool) => ({
                                value: tool,
                                label: tool || 'any',
                              }))}
                            />
                          ) : (
                            <input
                              aria-label={`${rule.id} condition ${conditionIndex + 1} value`}
                              value={String(condition.value)}
                              onChange={(event) =>
                                updateCondition(ruleIndex, conditionIndex, {
                                  value: parseConditionValue(condition.field, event.target.value),
                                })
                              }
                              className="input-field input-teal mt-1"
                            />
                          )}
                        </label>
                        <button
                          type="button"
                          aria-label={`Remove condition ${conditionIndex + 1} from ${rule.id}`}
                          onClick={() =>
                            updateRule(ruleIndex, {
                              conditions: rule.conditions.filter(
                                (_, itemIndex) => itemIndex !== conditionIndex,
                              ),
                            })
                          }
                          className="self-end rounded-lg p-1.5 text-[0.85rem] text-red-300/70 transition hover:bg-red-400/10 hover:text-red-300"
                        >
                          ×
                        </button>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="mt-3 flex flex-wrap items-end gap-3">
                  <button
                    type="button"
                    aria-label={`Add condition to ${rule.id}`}
                    onClick={() =>
                      updateRule(ruleIndex, {
                        conditions: [...rule.conditions, initialCondition()],
                      })
                    }
                    className="rounded-lg border border-teal-400/20 bg-teal-400/8 px-2.5 py-1 text-[0.72rem] font-medium text-teal-300 transition hover:bg-teal-400/15"
                  >
                    + AND condition
                  </button>
                  <label className="field-label min-w-[10rem] text-[0.7rem] text-[var(--text-secondary)]">
                    Target
                    <EncoderSelect
                      aria-label={`Target for ${rule.id}`}
                      value={rule.target}
                      onChange={(event) => updateRule(ruleIndex, { target: event.target.value })}
                      className="mt-1"
                      options={targets.map((target) => ({
                        value: target,
                        label: target === 'skip' ? 'Skip' : target,
                      }))}
                    />
                  </label>
                </div>
              </li>
            ))}
            <li className="rounded-xl border border-dashed border-white/15 bg-white/2 p-3">
              <span className="text-[0.74rem] font-medium text-[var(--text-secondary)]">
                Fallback
              </span>
              <label className="field-label mt-2 block text-[0.7rem] text-[var(--text-secondary)]">
                Fallback target
                <EncoderSelect
                  aria-label="Fallback target"
                  value={fallback}
                  onChange={(event) => {
                    pendingRules.current = null
                    setRulesDirty(true)
                    setFallback(event.target.value)
                  }}
                  className="mt-1"
                  options={targets.map((target) => ({
                    value: target,
                    label: target === 'skip' ? 'Skip' : target,
                  }))}
                />
              </label>
            </li>
          </ol>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => (
                (pendingRules.current = null),
                setRulesDirty(true),
                setDraftRules((current) => [
                  ...current,
                  {
                    id: nextRuleId(current),
                    conditions: [initialCondition()],
                    target: 'skip',
                  },
                ])
              )}
              className="rounded-lg border border-teal-400/25 bg-teal-400/10 px-3 py-2 text-[0.75rem] font-medium text-teal-300 transition hover:bg-teal-400/20"
            >
              Add rule
            </button>
            <button
              type="button"
              disabled={savingRules}
              onClick={() => void saveRules()}
              className="rounded-lg border border-teal-500/30 bg-teal-500/15 px-3 py-2 text-[0.75rem] font-medium text-teal-300 transition hover:bg-teal-500/25 disabled:opacity-50"
            >
              {savingRules ? 'Saving…' : 'Save rules'}
            </button>
          </div>

          <div className="rounded-xl border border-amber-400/30 bg-amber-400/8 px-4 py-3">
            <p className="text-[0.78rem] text-amber-200">
              Re-evaluate every media file in the configured watch folders with these rules.
            </p>
            {confirmReprocessAll ? (
              <div className="mt-2.5 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  disabled={reprocessAllActive || startingReprocessAll || !onStartReprocessAll}
                  onClick={() => void startReprocessAll()}
                  className="rounded-md border border-amber-300/40 bg-amber-300/15 px-3 py-1.5 text-[0.75rem] font-semibold text-amber-100 transition hover:bg-amber-300/25 disabled:opacity-50"
                >
                  {startingReprocessAll ? 'Starting…' : 'Confirm re-evaluate all media'}
                </button>
                <button
                  type="button"
                  disabled={startingReprocessAll}
                  onClick={() => setConfirmReprocessAll(false)}
                  className="rounded-md border border-white/20 bg-white/8 px-3 py-1.5 text-[0.75rem] font-semibold text-white/80 transition hover:bg-white/15 disabled:opacity-50"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                type="button"
                disabled={reprocessAllActive || startingReprocessAll || !onStartReprocessAll}
                onClick={() => setConfirmReprocessAll(true)}
                className="mt-2.5 rounded-md border border-amber-300/40 bg-amber-300/12 px-3 py-1.5 text-[0.75rem] font-semibold text-amber-100 transition hover:bg-amber-300/20 disabled:opacity-50"
              >
                Re-evaluate all media
              </button>
            )}
            {reprocessAllMessage && (
              <p role="status" className="mt-2 text-[0.72rem] text-amber-300">
                {reprocessAllMessage}
              </p>
            )}
          </div>

          <div className="rounded-xl border border-white/8 bg-white/3 p-3">
            <p className="field-label text-[var(--text-secondary)]">File to test</p>
            <label className="mt-2 block text-[0.7rem] text-[var(--text-secondary)]">
              Search directories
              <input
                value={directorySearch}
                onChange={(event) => setDirectorySearch(event.target.value)}
                placeholder="Filter directories…"
                className="input-field input-teal mt-1"
              />
            </label>
            <div className="mt-2">
              <DirectorySelect
                directories={directories}
                value={fileDirectory}
                base={selectedDirectory(fileDirectory).base}
                onChange={(path) => {
                  setFileDirectory(path)
                  changeTestPath('')
                }}
                onRefresh={() => setDirectorySearch('')}
                isLoading={directoriesLoading}
                color="teal"
                showBaseLabel
                ariaLabel="Test file directory"
                onClear={() => {
                  setFileDirectory('')
                  changeTestPath('')
                }}
              />
            </div>
            <label className="mt-2 block text-[0.7rem] text-[var(--text-secondary)]">
              Filter files
              <input
                value={fileSearch}
                onChange={(event) => setFileSearch(event.target.value)}
                placeholder="Filter files…"
                className="input-field input-teal mt-1"
              />
            </label>
            {files.length > 0 ? (
              <EncoderSelect
                aria-label="File to test selector"
                value={testPath}
                onChange={(event) => changeTestPath(event.target.value)}
                className="mt-2"
                options={[
                  { value: '', label: 'Select a media file…' },
                  ...files.map((file) => ({ value: file.path, label: file.name })),
                ]}
              />
            ) : (
              <input
                aria-label="File to test"
                value={testPath}
                onChange={(event) => changeTestPath(event.target.value)}
                placeholder="No files found; enter a path to test"
                className="input-field input-teal mt-2"
              />
            )}
            {filesLoading && (
              <p className="mt-1 text-[0.68rem] text-[var(--text-tertiary)]">Loading files…</p>
            )}
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={testing || !testPath.trim()}
                onClick={() => void runTest()}
                className="rounded-lg border border-teal-400/25 bg-teal-400/10 px-3 py-1.5 text-[0.75rem] font-medium text-teal-300 transition hover:bg-teal-400/20 disabled:opacity-50"
              >
                {testing ? 'Testing…' : 'Test file'}
              </button>
              <button
                type="button"
                disabled={reprocessing || !testPath.trim()}
                onClick={() => void reprocess()}
                className="rounded-lg border border-white/10 bg-white/4 px-3 py-1.5 text-[0.75rem] text-[var(--text-secondary)] transition hover:text-white disabled:opacity-50"
              >
                {reprocessing ? 'Reprocessing…' : 'Reprocess file'}
              </button>
            </div>

            {testResult && (
              <div className="mt-3 space-y-1 text-[0.72rem] text-[var(--text-secondary)]">
                <p>
                  Matched: {testResult.matched_rule ?? 'Fallback'} → {testResult.target}
                </p>
                <p>Evaluated: {testResult.evaluated.join(', ') || 'None'}</p>
                <p>Not evaluated: {testResult.not_evaluated.join(', ') || 'None'}</p>
                {Object.keys(testResult.facts).length > 0 && (
                  <pre className="overflow-x-auto rounded-lg bg-black/15 p-2 text-[0.66rem]">
                    {JSON.stringify(testResult.facts, null, 2)}
                  </pre>
                )}
              </div>
            )}
            {reprocessMessage && (
              <p role="status" className="mt-2 text-[0.72rem] text-teal-300">
                {reprocessMessage}
              </p>
            )}
          </div>
        </div>
      )}
    </section>
  )
}

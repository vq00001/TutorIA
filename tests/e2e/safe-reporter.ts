import type { FullResult, Reporter, TestCase, TestResult, TestStep } from '@playwright/test/reporter'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'

export default class SafeReporter implements Reporter {
  private failures = new Map<string,string>()
  private results: Array<{nodeId:string;outcome:string;seconds:number;failedPhase?:string;sourceLine?:number}>=[]
  onStepEnd(test:TestCase,_result:TestResult,step:TestStep) {
    if(step.category !== 'test.step' || !/^phase-[a-z-]+$/.test(step.title)) return
    console.log(`Browser phase: ${step.title} — ${step.error ? 'failed' : 'passed'}`)
    if(step.error) this.failures.set(test.id,step.title)
  }
  onTestEnd(test:TestCase,result:TestResult) {
    // Export only the fixed source filename's line number, never an error's
    // message/stack, which can contain DOM text, response bodies or tokens.
    const match = result.errors.map(e=>e.stack ?? '').join('\n').match(/learning-flow\.spec\.ts:(\d+):\d+/)
    this.results.push({nodeId:test.title,outcome:result.status,seconds:result.duration/1000,
      failedPhase:this.failures.get(test.id),sourceLine:match ? Number(match[1]) : undefined})
    // Error objects, URLs, screenshots, response bodies and traces never leave
    // the disposable browser container via this reporter.
    console.log(`Browser scenario: ${test.title} — ${result.status}`)
  }
  onEnd(result:FullResult) {
    const report={status:result.status,passed:this.results.filter(r=>r.outcome==='passed').length,
      failed:this.results.filter(r=>r.outcome==='failed'||r.outcome==='timedOut').length,
      skipped:this.results.filter(r=>r.outcome==='skipped').length,tests:this.results}
    writeFileSync(join(process.env.TEST_SUMMARY_DIR ?? '/tmp','tutoria-browser-summary.json'),JSON.stringify(report))
    console.log(JSON.stringify(report))
  }
}

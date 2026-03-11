# Problem Diagnosis and Fix Verification Methodology

**Core Principle: Never assume. Always verify. Evidence over intuition.**

This methodology applies to ALL problem-solving work: debugging, fixing, deploying, testing, or any system modification.

## Phase 1: Problem Diagnosis

### 1.1 Gather Evidence
- Collect concrete data: logs, task IDs, timestamps, error messages
- Reproduce the issue with specific examples
- Document the expected vs actual behavior
- **Never diagnose based on assumptions**

### 1.2 Trace the Root Cause
- Follow the data flow through the system
- Read actual code, don't assume how it works
- Use tools to inspect runtime state (Redis, process info, file contents)
- Identify the exact line/function causing the issue

### 1.3 Verify Understanding
- Explain the root cause with evidence
- Show the problematic code path
- Demonstrate why the bug occurs with concrete examples
- **If you can't explain it clearly, you don't understand it yet**

## Phase 2: Fix Implementation

### 2.1 Design the Fix
- Propose the minimal change that addresses the root cause
- Consider edge cases and side effects
- Explain why this fix solves the problem

### 2.2 Implement with Care
- Make targeted changes, avoid scope creep
- Preserve existing functionality
- Add comments explaining non-obvious logic

### 2.3 Syntax Verification
```bash
# Always verify syntax before claiming success
python3 -m py_compile <modified_file>
python3 -c "from module import function; print('Import OK')"
```

## Phase 3: Deployment Verification (CRITICAL)

### 3.1 Pre-Deployment State
```bash
# Record current state BEFORE making changes
ps -eo pid,lstart,cmd | grep <service>  # Process start time
stat -c %Y <file>                        # File modification time
redis-cli GET <key>                      # Data state
```

### 3.2 Execute Deployment
- Stop services cleanly
- Verify processes are actually stopped
- Start services
- Wait for initialization

### 3.3 Post-Deployment Verification (MANDATORY)
```bash
# 1. Verify process restarted
ps -eo pid,lstart,cmd | grep <service>
# Start time MUST be after code modification time

# 2. Verify code is loaded
python3 -c "import sys; sys.path.insert(0, '.'); from module import function; print(function.__code__.co_filename)"

# 3. Verify service is responsive
curl http://localhost:8000/health
```

### 3.4 Functional Verification (THE REAL TEST)
- Create a test case that reproduces the original bug
- Execute the test case against the deployed system
- Verify the bug is actually fixed
- Compare before/after behavior with concrete examples

**DO NOT report "fix deployed" until functional verification passes**

## Phase 4: Validation and Reporting

### 4.1 Evidence-Based Reporting
✓ **Good**: "Fix verified. Test task shows HIGH=file_high.safetensors, LOW=file_low.safetensors (previously both were HIGH)"
❌ **Bad**: "Fix deployed successfully"

✓ **Good**: "Restart failed. Process start time (Mar 10 16:51) is before code modification (Mar 11 04:03)"
❌ **Bad**: "Services restarted"

✓ **Good**: "Uncertain if fix works. Need to create a new task to test. Old tasks still show the bug."
❌ **Bad**: "Should be working now"

### 4.2 When Uncertain
- Explicitly state what you don't know
- Propose verification steps
- Never fill gaps with assumptions

### 4.3 Failed Verifications
- Report the specific failure immediately
- Don't continue as if it succeeded
- Investigate why verification failed

## Phase 5: Continuous Verification

### 5.1 Test with Real Data
- Use actual task IDs from production
- Compare before/after behavior
- Verify edge cases

### 5.2 Monitor for Regressions
- Check that old functionality still works
- Verify no new errors in logs
- Test related features

## Common Anti-Patterns to Avoid

❌ **Assumption-Based Work**
- "The restart script ran, so services must be restarted"
- "I modified the code, so the fix must be deployed"
- "The command succeeded, so it must have worked"

❌ **Incomplete Verification**
- Checking only that a process exists, not when it started
- Testing only the happy path, not the bug case
- Verifying syntax but not runtime behavior

❌ **Premature Success Declaration**
- Reporting success before testing
- Assuming deployment worked without verification
- Trusting command output without independent confirmation

## Verification Checklist

Before reporting any fix as complete, verify ALL of these:

- [ ] Root cause identified with evidence
- [ ] Fix implemented and syntax-checked
- [ ] Process/service actually restarted (timestamp verified)
- [ ] New code actually loaded (import test or version check)
- [ ] Original bug reproduced and confirmed fixed
- [ ] No regressions in related functionality
- [ ] Evidence documented (before/after comparison)

**Golden Rule: If you can't prove it with evidence, don't claim it.**

## Service Restart Protocol (Specific Application)

When restarting services after code changes:

```bash
# 1. Record current state
OLD_PID=$(ps aux | grep "uvicorn api.main:app" | grep -v grep | awk '{print $2}')
OLD_START=$(ps -p $OLD_PID -o lstart=)
FILE_MTIME=$(stat -c %Y api/services/workflow_builder.py)

# 2. Execute restart
bash scripts/stop_all.sh
sleep 2
bash scripts/start_all.sh
sleep 5

# 3. Verify new process
NEW_PID=$(ps aux | grep "uvicorn api.main:app" | grep -v grep | awk '{print $2}')
NEW_START=$(ps -p $NEW_PID -o lstart=)

# 4. Confirm PID changed and start time is recent
if [ "$OLD_PID" = "$NEW_PID" ]; then
    echo "ERROR: Process did not restart (same PID)"
    exit 1
fi

# 5. Test the actual fix
python3 -c "from api.services.workflow_builder import build_workflow; ..."
```

**Remember: A restart is not successful until you prove the new code is running and the bug is fixed.**

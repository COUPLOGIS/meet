# TeamLog v3 — 팀↔개인 과제 연동 + 텔레그램 알림 로드맵

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 팀 과제 중심의 양방향 개인 과제 연동 시스템 구축 + 텔레그램 사용자 초대·알림 통합

**Architecture:** 단일 HTML SPA (index.html) + localStorage ED 기반. `data.json`(wbs.json)은 GitHub Pages에서 정적 로드. 백엔드 없이 클라이언트 측에서 모든 로직 처리. 텔레그램 봇(@Teamlog2_bot)이 알림 브릿지 역할.

**Tech Stack:** Vanilla JS + CSS, localStorage, Telegram Bot API (via Hermes teamlog profile), GitHub Pages

---

## 현재 상태 분석

### 데이터 현황
| 파일 | 상태 | 내용 |
|------|:--:|------|
| `data.json` | 빈 파일 | tasks: 0, meetings: 0 |
| `wbs.json` | 빈 파일 | tasks: [], users: 1명(@hanpyo) |
| `users.json` | 보존 | @hanpyo(admin), @권용훈(member) |
| 회의록 HTML | **전부 소실** | 서버 이전 중 유실, 복구 불가 |
| `index.html` | v2 방금 배포 | light theme, links 기능 |

### 사용자 준비사항
- **권용훈님 텔레그램 ID 확인 필요** (@Teamlog2_bot으로 /start)
- 없으면 기존 `8256818775`가 권용훈님 ID인지 확인

---

## Phase 1: 데이터 모델 & 기초 인프라

### Task 1.1: 확장 데이터 모델 정의
**Files:** `data.json`, `wbs.json` 스키마 업데이트는 코드 레벨에서 처리 (파일은 그대로)

새로운 태스크 필드:
```js
{
  // 기존 필드 유지
  id, title, assignee, collaborators, parent_id, due_date, 
  status, priority, comments, meeting_id, created_by, tags, 
  links[], description, _h[], _fa[],
  
  // 신규 필드
  source: 'team' | 'personal',      // 과제 생성 출처
  mirrored_from: null | 'T001',     // 개인→팀: 원본 팀 과제 ID
  mirrored_to: ['P001', ...],       // 팀→개인: 미러링된 개인 과제 ID 목록
  mirror_type: null | 'full' | 'partial',  // full=전체과제, partial=하위과제만
  mirror_parent: null | 'T001',     // partial일 때 원본 부모 과제 ID
}
```

### Task 1.2: `all()` 헬퍼 함수 개선
**Files:** `index.html` 내 JS

- `all()`이 `ED`의 신규 필드를 올바르게 병합하도록 `ap()` 함수 업데이트
- `mirroredTasks()` — 특정 팀 과제의 모든 미러링된 개인 과제 반환
- `teamLinkedTasks()` — 현재 사용자의 팀 연결 과제만 필터링

---

## Phase 2: @멘션 → 개인 과제 자동 생성

### Task 2.1: 멘션 감지 로직
**Files:** `index.html` JS — `addComment()` 함수 확장

- 댓글 작성 시 `@username` 멘션 추출 (기존 `mentions` 배열 활용)
- 멘션된 사용자 목록을 `t.collaborators`에 자동 추가
- 신규 멘션 발생 시 `mirror_type='full'`로 개인 과제 자동 생성 트리거

### Task 2.2: 개인 과제 미러 생성
**Files:** `index.html` JS — `mirrorTask()` 신규 함수

```js
function mirrorTask(teamTaskId, targetUserId, mirrorType='full', mirrorParentId=null) {
  // 1. ED에서 새 개인 과제 생성
  // 2. source='personal', mirrored_from=teamTaskId
  // 3. assignee=targetUserId, status='대기중'
  // 4. 팀 과제 ED에 mirrored_to 배열에 추가
  // 5. 저장 후 텔레그램 알림 발송 (Phase 4)
}
```

### Task 2.3: 하위 과제 멘션 — 부모+하위 세트 미러
**Files:** `index.html` JS

- 하위 과제(has parent_id)에 @멘션 발생 시:
  - `mirror_type='partial'`로 개인 과제 생성
  - `mirror_parent`에 원본 부모 과제 ID 저장
  - 개인 뷰에서 부모+하위를 한 세트로 표시

---

## Phase 3: 개인 과제 뷰 재설계

### Task 3.1: My Tasks 섹션 분리
**Files:** `index.html` — `renderTasks()`, CSS

```
┌─ My Tasks ──────────────────────┐
│ 📌 팀 연결 과제 (3)              │  ← 상단 고정, 볼드+강조
│   T001 주간 매출 분석             │
│   └ P001 (미러) ⏳ Waiting        │
│   T003 식자재 발주 (partial)      │
│   └ T003-1 거래처 연락 ⏳         │
├──────────────────────────────────┤
│ 📋 개인 과제 (5)                  │
│   P005 블로그 원고 작성            │
│   ...                            │
└──────────────────────────────────┘
```

**구현:**
- `myLinkedTasks = all().filter(t => t.source==='personal' && t.mirrored_from)`
- `myPersonalTasks = all().filter(t => t.source==='personal' && !t.mirrored_from)`
- linked tasks 그룹을 먼저 렌더링, 구분선으로 분리

### Task 3.2: Partial 미러 표시
**Files:** `index.html` — `renderTasksTable()`

- `mirror_type='partial'`인 과제는 부모 과제 정보를 함께 표시
- "📌 T003 식자재 발주 → 거래처 연락" 형태
- 부모 클릭 시 팀 과제로 이동

### Task 3.3: 미러 배지
**Files:** `index.html` CSS + JS

- 미러링된 과제에는 `🔄` 또는 "팀연동" 배지 표시
- 색상: `var(--accent2)` (주황)

---

## Phase 4: 양방향 동기화

### Task 4.1: 팀 과제 수정 → 개인 과제 전파
**Files:** `index.html` — `ufx()` 함수 확장

```js
// ufx() 내부: 상태/제목/설명 변경 시
if (teamTask.mirrored_to) {
  teamTask.mirrored_to.forEach(personalId => {
    const p = ED[personalId] || {};
    p[f] = v; // 상태 동기화 (설정 가능)
    p._ha = [...(p._ha||[]), {t:now(), a:`팀 과제 ${f} → ${v}`, n:''}];
    ED[personalId] = p;
  });
}
```

### Task 4.2: 개인 과제 수정 → 팀 과제 전파
**Files:** `index.html` — `ufx()` 함수 확장

- 개인 미러 과제 수정 시 원본 팀 과제에도 반영
- 단, `assignee`는 전파하지 않음 (개인 과제의 담당자는 본인)

### Task 4.3: 댓글 양방향 동기화
**Files:** `index.html` — `addComment()` 확장

- 팀 과제 댓글 → 모든 미러 개인 과제에 복제
- 개인 미러 과제 댓글 → 원본 팀 과제에 복제
- 복제된 댓글에는 "[from 팀/개인]" 표시

---

## Phase 5: 개인→팀 승격

### Task 5.1: 승격 기능
**Files:** `index.html` — `renderTD()` 버튼 추가

- 개인 과제 상세 패널에 "팀 과제로 승격" 버튼
- 클릭 시:
  1. 새 팀 과제 ID 생성 (T번호)
  2. ED에 팀 과제로 등록 (`source='team'`)
  3. 기존 개인 과제는 `mirrored_from` 연결
  4. 회의 연결은 수동으로 (meeting_id prompt)

---

## Phase 6: 텔레그램 통합

### Task 6.1: 사용자 등록 플로우
**Files:** `meeting_bot.py` 또는 Hermes teamlog 프로필

- 새 사용자: @Teamlog2_bot에 `/start` → 자동 등록
- `users.json`에 `telegram_id`, `username`, `display_name`, `role: 'member'` 저장
- 어드민(@hanpyo)에게 "새 사용자 등록: @이름" 알림

### Task 6.2: 초대 시스템
- 어드민이 `/invite @username` 명령어로 초대
- 초대받은 사용자에게 텔레그램 DM: "TeamLog에 초대되었습니다. 수락하시겠습니까? [수락]"
- 수락 시 users.json에 등록 완료

### Task 6.3: @멘션 알림
- 팀 과제 댓글에서 @멘션 발생 → 해당 사용자 텔레그램으로 알림
- 알림 메시지: "📌 @한표님이 T001 '주간 매출 분석'에서 당신을 멘션했습니다"
- 알림 클릭 시 웹 대시보드 링크

### Task 6.4: 과제 변경 알림
- 연결된 과제 상태 변경 시 텔레그램 알림
- "🔄 T001 '주간 매출 분석' 상태가 '진행중' → '완료'로 변경되었습니다"

---

## Phase 7: 데이터 복구 & 안정화

### Task 7.1: 과거 회의록 데이터 (소실 — 복구 불가)
- 서버 이전 중 `~/meetings/`의 HTML 회의록 파일 유실
- 백업 없음 — 수동 재입력 필요
- **대안:** Google Sheets에 과거 데이터가 있다면 임포트 스크립트 제작

### Task 7.2: 백업 체계 구축
**Files:** GitHub Actions 또는 Hermes cron

- 매일 `data.json` + `wbs.json` + `users.json`을 `backups/` 디렉토리에 복사
- GitHub에 자동 커밋 (기존 cron 작업에 통합)

---

## 우선순위 & 일정

| Phase | 내용 | 예상 소요 | 의존성 |
|-------|------|:--:|--------|
| 1 | 데이터 모델 + 기초 | 30분 | 없음 |
| 2 | @멘션 → 미러 생성 | 1시간 | Phase 1 |
| 3 | 개인 과제 뷰 재설계 | 1시간 | Phase 1 |
| 4 | 양방향 동기화 | 1시간 | Phase 2,3 |
| 5 | 개인→팀 승격 | 30분 | Phase 4 |
| 6 | 텔레그램 통합 | 2시간 | Phase 2 |
| 7 | 데이터 복구·백업 | 30분 | 없음 |

**총 예상: 6~7시간**

---

## 준비사항 (사용자)

1. ✅ GitHub Pages 접속 가능한 환경
2. ⚠️ 권용훈님 텔레그램 ID 확인 (`8256818775`가 맞는지)
3. ⚠️ 과거 회의록 → Google Sheets에 보관 중이면 URL 공유
4. ⚠️ 추가 사용자 있으면 텔레그램 ID 확보

---

## 리스크 & 열린 질문

- **데이터 유실:** 회의록 HTML은 복구 불가, 수동 복구만 가능
- **localStorage 한계:** 브라우저별 5~10MB 제한, 대용량 데이터는 GitHub Pages에서 정적 호스팅
- **동기화 충돌:** 두 사용자가 동시에 같은 과제 수정 시 마지막 저장이 덮어씀 (현재 구조상 한계)
- **텔레그램 봇:** @Teamlog2_bot이 Hermes teamlog 프로필과 통합되어야 함

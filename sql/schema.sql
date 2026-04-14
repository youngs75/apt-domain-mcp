-- apt-domain-mcp PostgreSQL schema (Phase 0)
-- Requires PostgreSQL 15+ with pg_trgm (FTS analyzer choice deferred: pg_bigm or mecab-ko)

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- 1. Complex (단지 마스터)
-- ============================================================
CREATE TABLE IF NOT EXISTS complex (
    complex_id      TEXT PRIMARY KEY,           -- ULID
    name            TEXT NOT NULL,
    address         TEXT NOT NULL,
    sido            TEXT,                       -- 시·도
    sigungu         TEXT,                       -- 시·군·구
    units           INTEGER,                    -- 세대수
    buildings       INTEGER,                    -- 동수
    max_floors      INTEGER,
    use_approval_date DATE,                     -- 사용검사일
    management_type TEXT,                       -- 위탁/자치
    heating_type    TEXT,
    parking_slots   INTEGER,
    external_ids    JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {"kapt_code": "...", "bucheon_apt_seq": 73}
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_complex_name_trgm ON complex USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_complex_sigungu ON complex(sido, sigungu);

-- ============================================================
-- 2. Document (업로드 원본 파일 메타)
-- ============================================================
CREATE TABLE IF NOT EXISTS document (
    document_id     TEXT PRIMARY KEY,           -- ULID
    complex_id      TEXT NOT NULL REFERENCES complex(complex_id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,              -- 'regulation' | 'meeting' | 'audit' | 'notice' | 'ltrm_plan'
    title           TEXT NOT NULL,
    source_path     TEXT,                       -- 원본 파일 경로 또는 URL
    sha256          TEXT NOT NULL,              -- 원본 해시 (재인제스트 감지)
    pages           INTEGER,
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    uploaded_by     TEXT,                       -- 업로더 식별 (Phase 2)
    meta            JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_document_complex_kind ON document(complex_id, kind);
CREATE UNIQUE INDEX IF NOT EXISTS uq_document_sha ON document(complex_id, sha256);

-- ============================================================
-- 3. Regulation (관리규약) — 조문 단위 정규화 + append-only versioning
-- ============================================================
CREATE TABLE IF NOT EXISTS regulation_version (
    complex_id      TEXT NOT NULL REFERENCES complex(complex_id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,           -- 1, 2, 3, ... (개정 차수)
    effective_date  DATE NOT NULL,              -- 시행일
    source_document TEXT REFERENCES document(document_id),
    summary         TEXT,                       -- 개정 요약
    is_current      BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (complex_id, version)
);

-- 하나의 complex_id 당 is_current=true 는 최대 1개
CREATE UNIQUE INDEX IF NOT EXISTS uq_regulation_current
    ON regulation_version(complex_id) WHERE is_current = true;

CREATE TABLE IF NOT EXISTS regulation_article (
    complex_id      TEXT NOT NULL,
    version         INTEGER NOT NULL,
    article_number  TEXT NOT NULL,              -- "제1조", "제20조의2" 등 정규화된 형식
    article_seq     INTEGER NOT NULL,           -- 정렬용 (20, 201 for 20-2)
    chapter_number  INTEGER,
    chapter_title   TEXT,
    title           TEXT NOT NULL,              -- 조문 제목 "(목적)"
    body            TEXT NOT NULL,              -- 조문 본문 (항·호 포함)
    category        TEXT[],                     -- LLM 사전 태깅 (여러 개 가능)
    tags            TEXT[],
    referenced_articles TEXT[],                 -- 본문 내 다른 조문 참조
    referenced_laws TEXT[],                     -- "공동주택관리법 제X조" 등
    fts             tsvector,                   -- FTS 인덱스
    PRIMARY KEY (complex_id, version, article_number),
    FOREIGN KEY (complex_id, version) REFERENCES regulation_version(complex_id, version) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reg_article_current
    ON regulation_article(complex_id, article_seq);
CREATE INDEX IF NOT EXISTS idx_reg_article_fts
    ON regulation_article USING gin(fts);
CREATE INDEX IF NOT EXISTS idx_reg_article_category
    ON regulation_article USING gin(category);
CREATE INDEX IF NOT EXISTS idx_reg_article_body_trgm
    ON regulation_article USING gin(body gin_trgm_ops);

-- 개정 diff (조문 단위 변경 이력)
CREATE TABLE IF NOT EXISTS regulation_revision (
    complex_id      TEXT NOT NULL,
    from_version    INTEGER NOT NULL,
    to_version      INTEGER NOT NULL,
    article_number  TEXT NOT NULL,
    change_type     TEXT NOT NULL,              -- 'added' | 'removed' | 'modified'
    old_body        TEXT,                       -- modified/removed
    new_body        TEXT,                       -- modified/added
    reason          TEXT,                       -- 개정 사유
    PRIMARY KEY (complex_id, from_version, to_version, article_number),
    FOREIGN KEY (complex_id, from_version) REFERENCES regulation_version(complex_id, version),
    FOREIGN KEY (complex_id, to_version)   REFERENCES regulation_version(complex_id, version)
);

CREATE INDEX IF NOT EXISTS idx_reg_revision_article
    ON regulation_revision(complex_id, article_number);

-- ============================================================
-- 4. Meeting (입주자대표회의 회의록)
-- ============================================================
CREATE TABLE IF NOT EXISTS meeting (
    meeting_id      TEXT PRIMARY KEY,           -- ULID
    complex_id      TEXT NOT NULL REFERENCES complex(complex_id) ON DELETE CASCADE,
    meeting_date    DATE NOT NULL,
    meeting_type    TEXT NOT NULL,              -- '정기' | '임시'
    attendees_count INTEGER,
    quorum          INTEGER,
    source_document TEXT REFERENCES document(document_id),
    raw_text        TEXT,                       -- 원본 텍스트 전체
    fts             tsvector,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_meeting_complex_date
    ON meeting(complex_id, meeting_date DESC);
CREATE INDEX IF NOT EXISTS idx_meeting_fts
    ON meeting USING gin(fts);

CREATE TABLE IF NOT EXISTS meeting_decision (
    decision_id     TEXT PRIMARY KEY,
    meeting_id      TEXT NOT NULL REFERENCES meeting(meeting_id) ON DELETE CASCADE,
    complex_id      TEXT NOT NULL,              -- denormalized for query
    agenda_seq      INTEGER NOT NULL,           -- 안건 순번
    topic           TEXT NOT NULL,              -- 안건 제목
    category        TEXT[],                     -- LLM 태깅
    decision        TEXT NOT NULL,              -- 결정문
    result          TEXT,                       -- '가결' | '부결' | '보류'
    vote_for        INTEGER,
    vote_against    INTEGER,
    vote_abstain    INTEGER,
    related_articles TEXT[],                    -- 관련 관리규약 조문
    follow_up       TEXT,                       -- 후속조치
    fts             tsvector
);

CREATE INDEX IF NOT EXISTS idx_decision_complex_category
    ON meeting_decision(complex_id) INCLUDE (category);
CREATE INDEX IF NOT EXISTS idx_decision_fts
    ON meeting_decision USING gin(fts);
CREATE INDEX IF NOT EXISTS idx_decision_category_gin
    ON meeting_decision USING gin(category);

-- ============================================================
-- 5. Wiki Page (LLM 큐레이션, 파생 저장소)
-- ============================================================
CREATE TABLE IF NOT EXISTS wiki_page (
    complex_id      TEXT NOT NULL REFERENCES complex(complex_id) ON DELETE CASCADE,
    topic           TEXT NOT NULL,              -- slug: '주차', '관리비', '반려동물' ...
    title           TEXT NOT NULL,
    body_md         TEXT NOT NULL,              -- 마크다운 본문
    source_refs     JSONB NOT NULL,             -- [{type:'article', id:'제38조'}, {type:'meeting', id:'...'}]
    source_hash     TEXT NOT NULL,              -- 소스 셋 해시 (재생성 트리거용)
    generator_model TEXT,                       -- 어떤 LLM으로 생성했는지
    last_generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (complex_id, topic)
);

CREATE INDEX IF NOT EXISTS idx_wiki_complex ON wiki_page(complex_id);

-- ============================================================
-- 6. FTS trigger 함수 (tsvector 자동 갱신)
-- Phase 1에서 pg_bigm 또는 mecab-ko 도입 시 analyzer 교체
-- ============================================================
CREATE OR REPLACE FUNCTION trg_regulation_article_fts() RETURNS trigger AS $$
BEGIN
    NEW.fts := to_tsvector('simple', coalesce(NEW.title,'') || ' ' || coalesce(NEW.body,''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS regulation_article_fts_trg ON regulation_article;
CREATE TRIGGER regulation_article_fts_trg
    BEFORE INSERT OR UPDATE ON regulation_article
    FOR EACH ROW EXECUTE FUNCTION trg_regulation_article_fts();

CREATE OR REPLACE FUNCTION trg_meeting_decision_fts() RETURNS trigger AS $$
BEGIN
    NEW.fts := to_tsvector('simple',
        coalesce(NEW.topic,'') || ' ' || coalesce(NEW.decision,'') || ' ' || coalesce(NEW.follow_up,''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS meeting_decision_fts_trg ON meeting_decision;
CREATE TRIGGER meeting_decision_fts_trg
    BEFORE INSERT OR UPDATE ON meeting_decision
    FOR EACH ROW EXECUTE FUNCTION trg_meeting_decision_fts();

CREATE OR REPLACE FUNCTION trg_meeting_fts() RETURNS trigger AS $$
BEGIN
    NEW.fts := to_tsvector('simple', coalesce(NEW.raw_text,''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS meeting_fts_trg ON meeting;
CREATE TRIGGER meeting_fts_trg
    BEFORE INSERT OR UPDATE ON meeting
    FOR EACH ROW EXECUTE FUNCTION trg_meeting_fts();

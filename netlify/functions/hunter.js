// netlify/functions/hunter.js
// Netlify Serverless Function for Story Hunter (Fast, Resilient, Auto-Fallback)

const WUXIA_STRICT_SYSTEM_INSTRUCTION = `
당신은 정통무협(正統武俠) 웹소설 수석 기획 작가입니다.
[선협 절대 금지]: 도술, 선술, 영약 신선화, 강시, 환혼술, 기문진법 결계 등 선협 판타지는 100% 배제하고, 순수 정통무협(구파일방, 세가, 표국, 관부, 사파, 기경팔맥, 암기, 독공, 점혈, 협과 은원) 문법만을 적용하십시오.
[에피소드 단위 독립 치환]: 각 에피소드/사건 단위로 기막힌 정통무협 치환 아이디어를 다각도로 제시하십시오.
`;

async function callGeminiWithFallback(apiKey, prompt, systemInstruction = null, temperature = 0.7, preferredModel = "gemini-4-hydrogen") {
  const modelsToTry = [preferredModel];
  const priorityChain = ["gemini-4-hydrogen", "gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"];
  for (const m of priorityChain) {
    if (!modelsToTry.includes(m)) modelsToTry.push(m);
  }

  let lastError = null;
  for (const model of modelsToTry) {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${apiKey}`;
        const body = {
          contents: [{ parts: [{ text: prompt }] }],
          generationConfig: {
            temperature: temperature,
            responseMimeType: "application/json"
          }
        };

        if (systemInstruction) {
          body.systemInstruction = {
            parts: [{ text: systemInstruction }]
          };
        }

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 9000); // 9초 타임아웃 제한

        const res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: controller.signal
        });
        clearTimeout(timeoutId);

        if (!res.ok) {
          const errText = await res.text();
          throw new Error(`Gemini API Error (${res.status}): ${errText}`);
        }

        const data = await res.json();
        const text = data.candidates?.[0]?.content?.parts?.[0]?.text || "";
        if (text) return { text, usedModel: model };
      } catch (err) {
        lastError = err;
        // 잠시 대기 후 재시도 또는 다음 모델로 전환
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    }
  }
  throw lastError || new Error("Gemini API 호출에 실패했습니다.");
}

function parseJSON(text) {
  let cleaned = text.trim();
  if (cleaned.includes("```json")) {
    cleaned = cleaned.split("```json")[1].split("```")[0].trim();
  } else if (cleaned.includes("```")) {
    cleaned = cleaned.split("```")[1].split("```")[0].trim();
  }
  return JSON.parse(cleaned);
}

function generateMarkdownReport(work, combinedData, usedModel) {
  let md = `# [정통무협 에피소드 발굴 리포트] ${work.title}\n`;
  md += `- **생성 일시**: ${new Date().toLocaleString("ko-KR")}\n`;
  md += `- **원작 정보**: ${work.title} (${work.year || ""}, ${work.country || ""}) | ${work.media || ""} (${work.genre || ""})\n`;
  md += `- **활용 AI 모델**: ${usedModel || "Gemini 3.5"}\n\n---\n\n`;
  md += `## 1. 원작 및 추천 배경\n**전체 줄거리 요약**:\n${work.summary || ""}\n\n`;
  md += `**추천 이유**:\n${work.recommendation_reason || ""}\n\n---\n\n`;
  md += `## 2. 핵심 에피소드 분석 및 정통무협 치환 아이디어\n`;
  md += `**작품 핵심 전제**: ${combinedData.overall_premise || ""}\n\n`;

  const eps = combinedData.episodes || [];
  eps.forEach((ep, i) => {
    md += `### ❖ [사건 ${i + 1}] ${ep.episode_title}\n`;
    md += `**📖 원작 에피소드 스토리**:\n${ep.story_summary}\n\n`;
    md += `**✨ 핵심 재미 포인트**: ${ep.key_attraction}\n\n`;
    md += `#### ⚔️ 정통무협 치환 아이디어 (선협 배제)\n`;
    md += `**상황 컨셉**: ${ep.wuxia_situation_concept || ""}\n\n`;
    (ep.adaptation_ideas || []).forEach((idea, j) => {
      md += `**[방향 ${j + 1}] ${idea.direction_name}**\n`;
      md += `- **치환 아이디어**: ${idea.idea_description}\n`;
      md += `- **사이다/클라이맥스 훅**: ${idea.novel_writing_hook}\n\n`;
    });
    if (ep.orthodox_martial_elements) {
      md += `**💡 추천 무협 기믹/요소**: ${ep.orthodox_martial_elements}\n\n`;
    }
    md += `---\n\n`;
  });
  return md;
}

exports.handler = async (event, context) => {
  if (event.httpMethod === "OPTIONS") {
    return {
      statusCode: 200,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS"
      },
      body: ""
    };
  }

  const apiKey = process.env.GEMINI_API_KEY || process.env.GOOGLE_API_KEY;
  if (!apiKey) {
    return {
      statusCode: 500,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
      body: JSON.stringify({ error: "GEMINI_API_KEY가 설정되지 않았습니다." })
    };
  }

  try {
    const body = event.body ? JSON.parse(event.body) : {};
    const { action } = body;

    // 0. 브라우저 직접 고품질 호출을 위한 키 제공 (Netlify 10초 타임아웃 502/504 원천 회피)
    if (action === "get_api_key") {
      return {
        statusCode: 200,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
        body: JSON.stringify({ success: true, apiKey: apiKey })
      };
    }

    // 1. [신규] 1작품 원스톱 자동 발굴 및 정통무협 즉시 변환 (서버리스 직접 실행 시)
    if (action === "auto_single") {
      const theme = body.theme || "";
      
      // Step A: 1개 작품 발굴 (3.5-flash-lite로 초고속 1초 처리)
      let discoverPrompt = `웹소설 에피소드 소재로 활용하기에 최적화된 숨은 명작 1개를 엄선하여 발굴하십시오.
시대를 초월한 고전, 상업적 성공은 못했으나 스토리가 경이로운 명작, 컬트 추리/스릴러/서부극/하이스트 등 매체 불문.`;
      if (theme) discoverPrompt += `\n요청 테마/키워드: ${theme}\n`;
      discoverPrompt += `
반드시 아래 JSON 형식(단일 객체)으로만 반환하십시오:
{
  "title": "작품명 (원제 포함)",
  "year": "연도",
  "country": "국가",
  "media": "매체",
  "genre": "장르",
  "summary": "핵심 줄거리 (3줄)",
  "recommendation_reason": "스토리적 추천 이유"
}`;
      const { text: discRaw } = await callGeminiWithFallback(apiKey, discoverPrompt, null, 0.8, "gemini-3.5-flash-lite");
      const work = parseJSON(discRaw);

      // Step B: 정통무협 즉시 치환 (3.5-flash 기본, 실패 시 3.5-flash-lite 폴백)
      const adaptPrompt = `
작품 [${work.title}]의 핵심 사건 2개를 추출하여, 
1) 발단부터 결말까지 술술 읽히는 서사형 스토리 요약본을 작성하고,
2) 각 사건을 정통무협(仙俠 판타지 100% 배제)으로 치환할 수 있는 실전 치환 아이디어들을 도출하십시오.
참고 줄거리: ${work.summary || ""}

반드시 아래 JSON 형식으로 반환하십시오:
{
  "work_title": "${work.title}",
  "overall_premise": "작품 핵심 전제 (2줄 요약)",
  "episodes": [
    {
      "episode_title": "사건/에피소드 소제목",
      "story_summary": "한 편의 흥미진진한 이야기처럼 발단-갈등-반전-해결을 유기적으로 풀어낸 서사형 스토리 (2단락)",
      "key_attraction": "핵심 재미/긴장감 포인트",
      "wuxia_situation_concept": "무협으로 변환할 핵심 상황 컨셉 (1줄)",
      "adaptation_ideas": [
        {
          "direction_name": "치환 방향",
          "idea_description": "구체적인 무협 치환 아이디어",
          "novel_writing_hook": "웹소설 집필 시 사이다 훅"
        }
      ],
      "orthodox_martial_elements": "어울리는 정통무협 기믹"
    }
  ]
}`;
      const { text: adaptRaw, usedModel } = await callGeminiWithFallback(apiKey, adaptPrompt, WUXIA_STRICT_SYSTEM_INSTRUCTION, 0.75, "gemini-4-hydrogen");
      const adaptData = parseJSON(adaptRaw);
      const markdown = generateMarkdownReport(work, adaptData, usedModel);

      return {
        statusCode: 200,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
        body: JSON.stringify({
          success: true,
          work: work,
          markdown: markdown,
          usedModel: usedModel
        })
      };
    }

    // 2. 단일 작품 치환 (직접 입력 탭 또는 이전 발굴 작품 치환)
    if (action === "adapt") {
      const work = body.work;
      if (!work || !work.title) {
        return { statusCode: 400, headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }, body: JSON.stringify({ error: "작품 정보가 필요합니다." }) };
      }

      const adaptPrompt = `
작품 [${work.title}]의 핵심 사건 2개를 추출하여, 
1) 발단부터 결말까지 술술 읽히는 서사형 스토리 요약본을 작성하고,
2) 각 사건을 정통무협(仙俠 판타지 100% 배제)으로 치환할 수 있는 실전 치환 아이디어들을 도출하십시오.
참고 줄거리: ${work.summary || ""}

반드시 아래 JSON 형식으로 반환하십시오:
{
  "work_title": "${work.title}",
  "overall_premise": "작품 핵심 전제 (2줄 요약)",
  "episodes": [
    {
      "episode_title": "사건/에피소드 소제목",
      "story_summary": "한 편의 흥미진진한 이야기처럼 발단-갈등-반전-해결을 유기적으로 풀어낸 서사형 스토리 (2단락)",
      "key_attraction": "핵심 재미/긴장감 포인트",
      "wuxia_situation_concept": "무협으로 변환할 핵심 상황 컨셉 (1줄)",
      "adaptation_ideas": [
        {
          "direction_name": "치환 방향",
          "idea_description": "구체적인 무협 치환 아이디어",
          "novel_writing_hook": "웹소설 집필 시 사이다 훅"
        }
      ],
      "orthodox_martial_elements": "어울리는 정통무협 기믹"
    }
  ]
}`;
      const { text: adaptRaw, usedModel } = await callGeminiWithFallback(apiKey, adaptPrompt, WUXIA_STRICT_SYSTEM_INSTRUCTION, 0.75, "gemini-4-hydrogen");
      const adaptData = parseJSON(adaptRaw);
      const markdown = generateMarkdownReport(work, adaptData, usedModel);

      return {
        statusCode: 200,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
        body: JSON.stringify({
          success: true,
          work: work,
          markdown: markdown,
          usedModel: usedModel
        })
      };
    }

    return { statusCode: 400, headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }, body: JSON.stringify({ error: "알 수 없는 action입니다." }) };
  } catch (err) {
    return {
      statusCode: 500,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
      body: JSON.stringify({ error: err.message || "서버 처리 중 오류가 발생했습니다." })
    };
  }
};

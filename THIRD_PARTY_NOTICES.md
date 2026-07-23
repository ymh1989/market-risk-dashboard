# 제3자 소프트웨어 고지

## webgl-fluid 0.3.9

- 용도: Field Lab 눈 모드의 WebGL 유체장 시뮬레이션
- 저작권자: Cloyd Lau
- 라이선스: MIT
- 라이선스 전문: `src/vendor/webgl-fluid.LICENSE`

이 패키지는 Pavel Dobryakov의 `WebGL Fluid Simulation`을 기반으로 합니다.

- 원저작권자: Pavel Dobryakov
- 라이선스: MIT
- 원본 라이선스 전문: `src/vendor/webgl-fluid-origin.LICENSE`

배포 파일은 재현성과 외부 장애 격리를 위해 `src/vendor/webgl-fluid.mjs`에 고정해 보관합니다.

## Three.js 0.185.1

- 용도: Ocean Lab의 3D 바다 메시, 카메라, 레이캐스팅과 WebGL 렌더링
- 저작권자: Three.js Authors
- 라이선스: MIT
- 라이선스 전문: `src/vendor/three.LICENSE`

배포 파일은 `src/vendor/three.module.min.js`, `src/vendor/three.core.min.js`에 같은 버전으로 고정해 보관합니다.

## 해양 스펙트럼 참고 방법론

- Jerry Tessendorf, [`Simulating Ocean Water`](https://people.computing.clemson.edu/~jtessen/reports/papers_files/coursenotes2002.pdf) (1999-2001)
- George Bolba, [`Oceans: Theory to Implementation`](https://gikster.dev/posts/Ocean-Simulation/) (CC BY 4.0)
- 참고 범위: JONSWAP 스펙트럼, 분산관계, 수평·수직 변위, 기울기와 Jacobian 기반 포말

브라우저 구현은 원문의 코드나 512×512 FFT 구현을 복제하지 않습니다. 동일한 공개 수식을 고정 시드의 희소 스펙트럼으로 다시 작성해 Three.js 꼭짓점 셰이더에서 합성합니다.

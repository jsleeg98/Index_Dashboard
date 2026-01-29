## 🛠 WSL 환경에서 npm 전역 설치(EACCES) 오류 해결

### 문제 상황
WSL 환경에서 npm 전역 설치 시 다음과 같은 오류가 발생할 수 있다.

```

npm ERR! code EACCES
npm ERR! syscall mkdir
npm ERR! path /usr/local/lib/node_modules
npm ERR! Error: EACCES: permission denied

```

예:
```

npm install -g opencode-ai

```

---

### 원인
- npm 전역(global) 설치의 기본 경로는 `/usr/local`
- 해당 디렉토리는 root 권한이 필요
- 일반 사용자 권한으로 전역 설치 시 쓰기 권한 부족으로 `EACCES` 오류 발생

---

### 해결 방법 (권장)

#### 1. npm 전역 설치 경로를 home 디렉토리로 변경
```

mkdir -p ~/.npm-global
npm config set prefix '~/.npm-global'

```

#### 2. PATH에 전역 실행 파일 경로 추가
```

echo 'export PATH=$HOME/.npm-global/bin:$PATH' >> ~/.bashrc
source ~/.bashrc

```

> zsh 사용 시 `.zshrc`에 추가

#### 3. 기존 전역 패키지 제거 (기존 경로에 설치된 경우)
```

sudo npm uninstall -g opencode-ai

```

#### 4. 패키지 재설치
```

npm install -g opencode-ai

```

---

### 설치 확인
```

npm config get prefix
which opencode
opencode --version

```

정상 출력 예:
```

/home/<username>/.npm-global
/home/<username>/.npm-global/bin/opencode

```

---

### 주의 사항
- prefix 변경 후 기존 전역 패키지는 자동으로 이전되지 않음
- prefix 변경 이후에는 전역 패키지 재설치 필요
- 제거 없이 재설치할 경우 동일 패키지가 여러 경로에 존재할 수 있음

```

type -a opencode

```

---

### 한 줄 정리
**WSL 환경에서는 npm 전역 설치 prefix를 home 디렉토리로 설정하는 것이 가장 안전하다.**
```

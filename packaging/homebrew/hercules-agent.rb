class HerculesAgent < Formula
  include Language::Python::Virtualenv

  desc "Self-improving AI agent that creates skills from experience"
  homepage "https://github.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-"
  # Stable source should point at the semver-named sdist asset attached by
  # scripts/release.py, not the CalVer tag tarball.
  url "https://github.com/mintoriakamoto/Hercules-Agent-Hermes-Brother-/releases/download/v2026.7.18/hercules_agent-1.0.0.tar.gz"
  sha256 "a0135a62faf3b2606de075931e28b07a45e161513c59733a181a79036811caef"
  license "MIT"

  depends_on "certifi" => :no_linkage
  depends_on "cryptography" => :no_linkage
  depends_on "libyaml"
  # pyproject.toml caps requires-python at <3.14 (Rust transitives lack
  # cp314 wheels) — keep this on the newest supported minor.
  depends_on "python@3.13"

  pypi_packages ignore_packages: %w[certifi cryptography pydantic]

  # Refresh resource stanzas after bumping the source url/version:
  #   brew update-python-resources --print-only hercules-agent

  def install
    venv = virtualenv_create(libexec, "python3.14")
    venv.pip_install resources
    venv.pip_install buildpath

    pkgshare.install "skills", "optional-skills"

    %w[hercules hercules-agent hercules-acp].each do |exe|
      next unless (libexec/"bin"/exe).exist?

      (bin/exe).write_env_script(
        libexec/"bin"/exe,
        HERCULES_BUNDLED_SKILLS: pkgshare/"skills",
        HERCULES_OPTIONAL_SKILLS: pkgshare/"optional-skills",
        HERCULES_MANAGED: "homebrew"
      )
    end
  end

  test do
    assert_match "Hercules Agent v#{version}", shell_output("#{bin}/hercules version")

    managed = shell_output("#{bin}/hercules update 2>&1")
    assert_match "managed by Homebrew", managed
    assert_match "brew upgrade hercules-agent", managed
  end
end

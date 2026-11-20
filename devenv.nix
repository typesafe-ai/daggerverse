{
  pkgs,
  inputs,
  ...
}: let
  pkgs-unstable = import inputs.nixpkgs-unstable {inherit (pkgs) system;};
  # dagger/nix only packages stable releases; track the 1.0 betas until 1.0.0 lands there.
  dagger = inputs.dagger.packages.${pkgs.system}.dagger.overrideAttrs (_: let
    version = "1.0.0-beta.27";
    platform =
      {
        x86_64-linux = "linux_amd64";
        aarch64-linux = "linux_arm64";
        x86_64-darwin = "darwin_amd64";
        aarch64-darwin = "darwin_arm64";
      }.${
        pkgs.system
      };
  in {
    inherit version;
    src = pkgs.fetchurl {
      url = "https://dl.dagger.io/dagger/releases/${version}/dagger_v${version}_${platform}.tar.gz";
      sha256 =
        {
          linux_amd64 = "de2a3ca569c3a955d5ce98d2c4879b67a4459c394a7e1684261ba7152bdb6865";
          linux_arm64 = "1ef003a4522d4f39c862d6dd360bc2916a605f87aad3dce4988c01f4bcd73358";
          darwin_amd64 = "14b63d665c0bd71a2882b7e90e40dcf96aca27ebee6964d30c8802cf509bc29d";
          darwin_arm64 = "5efd7f5cec39093dd10d4035aae8ee87f82403606c0a6d958d74a0edee04171a";
        }.${
          platform
        };
    };
  });
in {
  # https://devenv.sh/packages/
  packages = [
    dagger
    pkgs.pinact
    pkgs.zizmor
    pkgs.prek
    pkgs.git-cliff
    pkgs.just
    pkgs-unstable.uv
    # gofmt for the prek hook on the Go modules.
    pkgs.go
  ];
}

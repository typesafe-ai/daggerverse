{
  pkgs,
  inputs,
  ...
}: let
  pkgs-unstable = import inputs.nixpkgs-unstable {inherit (pkgs) system;};
  # dagger/nix only packages stable releases; track the 1.0 betas until 1.0.0 lands there.
  dagger = inputs.dagger.packages.${pkgs.system}.dagger.overrideAttrs (_: let
    version = "1.0.0-beta.30";
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
          linux_amd64 = "e3e2de23d5b83914ba059c248a497e6a50d82aa3b679aa0710e2969436a2d88f";
          linux_arm64 = "130ab71fdbd6dda95aa9d6615cab3f1342250a5af132fe5a049da1ef5b530e04";
          darwin_amd64 = "c916ebcc3a107ebd6e5f0ab04eba20b53617c0353d90ca7b65c67819c5fda3df";
          darwin_arm64 = "5f264e494ed8ce0fde40a5fff656f0d723e0c8d96adfb81b8862b8ce9f5ddee9";
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

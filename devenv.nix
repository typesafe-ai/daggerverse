{
  pkgs,
  inputs,
  ...
}: let
  pkgs-unstable = import inputs.nixpkgs-unstable {inherit (pkgs) system;};
in {
  # https://devenv.sh/packages/
  packages = [
    inputs.dagger.packages.${pkgs.system}.dagger
    pkgs.pinact
    pkgs.zizmor
    pkgs.prek
    pkgs.git-cliff
    pkgs-unstable.uv
  ];
}

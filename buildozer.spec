[app]
title = Photo3D
package.name = photo3d
package.domain = org.localphoto3d
source.dir = .
source.include_exts = py,png,jpg,jpeg,json,kv,md
version = 0.3.0
requirements = python3,kivy==2.3.0,pyjnius,android,androidstorage4kivy
orientation = all
fullscreen = 0
android.permissions = READ_MEDIA_IMAGES,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE
android.api = 34
android.minapi = 24
android.archs = arm64-v8a

android.ndk = 25b

android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1


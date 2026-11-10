FROM docker.io/library/maven:3.9-eclipse-temurin-21 AS build
WORKDIR /source

# Public gematik reference source, pinned and verified independently of the image tag.
ADD https://codeload.github.com/gematik/app-gemSekIdp/tar.gz/2908032fbfcbc185688f6d479cfe952d3708bfe8 /tmp/source.tar.gz
ARG MAVEN_CA_CERT
ARG REFERENCE_MODULE=gsi-server
RUN echo "f68116860d324805b194a4d6fa48667e35b1548abed1774784cf54b916f668ad  /tmp/source.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/source.tar.gz --strip-components=1 -C /source \
    && if [ -n "$MAVEN_CA_CERT" ]; then \
         cp "$JAVA_HOME/lib/security/cacerts" /tmp/maven-cacerts; \
         keytool -importcert -noprompt -alias extra-build-ca -file "$MAVEN_CA_CERT" \
           -keystore /tmp/maven-cacerts -storepass changeit; \
         export MAVEN_OPTS="-Djavax.net.ssl.trustStore=/tmp/maven-cacerts -Djavax.net.ssl.trustStorePassword=changeit"; \
       fi \
    && case "$REFERENCE_MODULE" in gsi-server|gsi-fedmaster) ;; *) exit 1 ;; esac \
    && mvn -B -ntp -pl "$REFERENCE_MODULE" -am package -Dskip.unittests \
    && rm -f /tmp/maven-cacerts

FROM docker.io/library/eclipse-temurin:21-jre
ARG REFERENCE_MODULE=gsi-server
WORKDIR /app
COPY --from=build /source/${REFERENCE_MODULE}/target/${REFERENCE_MODULE}-8.5.1.jar /app/reference-server.jar
USER 10001:10001
ENTRYPOINT ["java", "-jar", "/app/reference-server.jar"]
